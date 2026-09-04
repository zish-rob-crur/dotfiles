#!/usr/bin/env python3
"""Give tmux windows short, meaningful names with one batched `codex exec` call.

Every pane of a window contributes its directory, command, git branch and
title; the model returns one name per window. A window is renamed only when
some pane carries a signal beyond the directory (a branch or a title), and
windows the user renamed by hand are left alone. Names set here are tracked in
the window options @llm-name and @llm-key so a later run can tell them apart
from manual renames and skip windows whose context has not changed.

    window-namer.py            name changed windows now
    window-namer.py --print    show which windows would be sent, no codex call
    window-namer.py --propose  ask codex and print the names without applying
    window-namer.py --apply    apply the last proposal
    window-namer.py --daemon   poll and name automatically until tmux exits
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "tmux-window-namer"
PROPOSAL_PATH = STATE_DIR / "proposal.json"
DAEMON_INTERVAL = float(os.environ.get("TMUX_WINDOW_NAMER_INTERVAL", "30"))
CODEX_MODEL = os.environ.get("TMUX_WINDOW_NAMER_MODEL", "gpt-5.4-mini")
CODEX_TIMEOUT = 120
MAX_NAME_LENGTH = 12
FIELD_SEP = "\x1f"

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "names": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                "required": ["id", "name"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["names"],
    "additionalProperties": False,
}

PROMPT = """You name tmux windows so a developer can tell them apart at a glance.

Each window lists its panes. Reply with one name per window: at most {max_len}
characters, only lowercase ascii letters, digits and hyphens. Describe the task
or topic the panes share, not the repository. Names must be distinct from each
other and from the taken names. Keep a window's current name when its context
still fits it.

Taken names: {taken}

Windows:
{windows}
"""


@dataclass
class Pane:
    path: str
    command: str
    title: str
    active: bool
    branch: str = ""
    repo: str = ""


@dataclass
class Window:
    id: str
    name: str
    auto_rename: bool
    llm_name: str
    llm_key: str
    icon: str
    panes: list[Pane] = field(default_factory=list)

    @property
    def owned(self) -> bool:
        """True when the current name was set by this script."""
        return not self.auto_rename and bool(self.llm_name) and self.name == self.llm_name

    @property
    def signal(self) -> bool:
        return any(p.branch or p.title for p in self.panes)

    @property
    def key(self) -> str:
        return FIELD_SEP.join(f"{p.path}|{p.branch}|{p.title}" for p in self.panes) + FIELD_SEP + self.icon

    @property
    def repo(self) -> str:
        """Repository of the active pane, else the first pane inside a repository."""
        return next((p.repo for p in sorted(self.panes, key=lambda p: not p.active) if p.repo), "")

    @property
    def topic(self) -> str:
        """The model-chosen part of the current name, without icon and prefix."""
        if not self.owned:
            return ""
        return self.name.split(" ")[-1].rsplit(":", 1)[-1]  # "<icon> <prefix>:<topic>"


def tmux(args: list[str]) -> str:
    return subprocess.run(["tmux", *args], capture_output=True, text=True).stdout


@lru_cache(maxsize=None)
def git_info(path: str) -> tuple[str, str]:
    """(branch, repo name) for a path, empty strings outside a repository."""
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "", ""
    lines = result.stdout.splitlines()
    if len(lines) != 2:
        return "", ""
    branch = "" if lines[0] == "HEAD" else lines[0]
    repo = os.path.basename(lines[1]).split(".", 1)[0]  # worktree dirs look like repo.branch
    return branch, repo


@lru_cache(maxsize=None)
def prefix_overrides() -> dict[str, str]:
    """Repo -> prefix pairs from the tmux option @window-namer-prefixes, e.g. "my-long-repo-name=mono other=oth"."""
    pairs = tmux(["show", "-gv", "@window-namer-prefixes"]).split()
    return dict(pair.split("=", 1) for pair in pairs if "=" in pair)


def project_prefix(repo: str) -> str:
    if not repo:
        return ""
    if repo in prefix_overrides():
        return prefix_overrides()[repo]
    words = [w for w in re.split(r"[-_]+", repo.lower()) if w]
    if len(words) >= 2:
        return "".join(w[0] for w in words)[:4]
    return words[0][:4] if words else ""


def compose(icon: str, prefix: str, topic: str) -> str:
    body = f"{prefix}:{topic}" if prefix else topic
    return f"{icon} {body}" if icon else body


def clean_title(title: str, path: str) -> str:
    """Drop titles that carry no information beyond the directory."""
    title = re.sub(r"^[\s✳⠁-⣿]+", "", title).strip()
    if not title or title.endswith("...") or title == socket.gethostname():
        return ""
    if title in {os.path.basename(path), "~"}:
        return ""
    return title


def clean_command(command: str) -> str:
    return "claude" if re.fullmatch(r"\d+\.\d+\.\d+", command) else command


def clean_icon(icon: str) -> str:
    """Keep the leading tool icon from @pane-window-icon; drop layout suffixes like ×2 or │."""
    icon = re.split(r"[×│/\d]", icon, maxsplit=1)[0].strip()
    return "" if icon == "·" else icon


def list_windows() -> list[Window]:
    fields = [
        "#{window_id}",
        "#{window_name}",
        "#{automatic-rename}",
        "#{@llm-name}",
        "#{@llm-key}",
        "#{@pane-window-icon}",
        "#{pane_active}",
        "#{pane_current_path}",
        "#{pane_current_command}",
        "#{?#{@codex-session-title},#{@codex-session-title},#{pane_title}}",
    ]
    git_info.cache_clear()  # branches and overrides may change between daemon rounds
    prefix_overrides.cache_clear()
    windows: dict[str, Window] = {}
    for line in tmux(["list-panes", "-a", "-F", FIELD_SEP.join(fields)]).splitlines():
        parts = line.split(FIELD_SEP)
        if len(parts) != len(fields):
            continue
        window = windows.setdefault(
            parts[0],
            Window(
                id=parts[0],
                name=parts[1].strip(),
                auto_rename=parts[2] == "1",
                llm_name=parts[3],
                llm_key=parts[4],
                icon=clean_icon(parts[5]),
            ),
        )
        pane = Pane(path=parts[7], command=clean_command(parts[8]), title=clean_title(parts[9], parts[7]), active=parts[6] == "1")
        pane.branch, pane.repo = git_info(pane.path)
        window.panes.append(pane)
    return list(windows.values())


def sanitize(name: str) -> str:
    name = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    name = re.sub(r"-{2,}", "-", name)
    if len(name) > MAX_NAME_LENGTH:
        cut = name.rfind("-", 4, MAX_NAME_LENGTH + 1)  # prefer a word boundary
        name = name[: cut if cut > 0 else MAX_NAME_LENGTH]
    return name.rstrip("-")


def plan(windows: list[Window]) -> tuple[list[Window], list[Window]]:
    """Split windows into those to name and those to hand back to tmux."""
    to_name, to_release = [], []
    for window in windows:
        if not (window.auto_rename or window.owned):
            continue  # renamed by hand
        if not window.signal:
            if window.owned:
                to_release.append(window)
            continue
        if window.owned and window.llm_key == window.key:
            continue
        to_name.append(window)
    return to_name, to_release


def describe(window: Window) -> dict:
    info: dict = {"id": window.id}
    if window.topic:
        info["current_name"] = window.topic
    info["panes"] = []
    for pane in window.panes:
        entry: dict[str, str | bool] = {"dir": pane.path.replace(str(Path.home()), "~"), "command": pane.command}
        if pane.branch:
            entry["branch"] = pane.branch
        if pane.title:
            entry["title"] = pane.title
        if pane.active:
            entry["active"] = True
        info["panes"].append(entry)
    return info


def ask_codex(to_name: list[Window], taken: list[str]) -> dict[str, str]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=STATE_DIR) as tmp:
        schema = Path(tmp) / "schema.json"
        schema.write_text(json.dumps(OUTPUT_SCHEMA))
        output = Path(tmp) / "out.json"
        prompt = PROMPT.format(
            max_len=MAX_NAME_LENGTH,
            taken=", ".join(sorted(taken)) or "(none)",
            windows=json.dumps([describe(w) for w in to_name], ensure_ascii=False, indent=1),
        )
        try:
            subprocess.run(
                [
                    "codex", "exec",
                "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
                "-s", "read-only", "--color", "never",
                "-C", tmp,
                "-m", CODEX_MODEL, "-c", "model_reasoning_effort=low",
                    "--output-schema", str(schema), "-o", str(output),
                    prompt,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=CODEX_TIMEOUT,
            )
        except FileNotFoundError:
            print("window-namer: codex CLI not found on PATH", file=sys.stderr)
            return {}
        except subprocess.TimeoutExpired:
            print(f"window-namer: codex exec timed out after {CODEX_TIMEOUT}s", file=sys.stderr)
            return {}
        if not output.exists():
            print("window-namer: codex returned nothing (not logged in or offline?)", file=sys.stderr)
            return {}
        try:
            entries = json.loads(output.read_text()).get("names", [])
        except (json.JSONDecodeError, AttributeError):
            return {}
    names = {}
    for entry in entries:
        name = sanitize(str(entry.get("name", "")))
        if name:
            names[str(entry.get("id", ""))] = name
    return names


def release(window: Window) -> None:
    tmux(["set", "-w", "-t", window.id, "-u", "@llm-name"])
    tmux(["set", "-w", "-t", window.id, "-u", "@llm-key"])
    tmux(["set", "-w", "-t", window.id, "automatic-rename", "on"])


def apply(proposal: dict[str, dict[str, str]]) -> None:
    """proposal maps window id -> {"name": ..., "key": ...}."""
    for window_id, entry in proposal.items():
        tmux(["rename-window", "-t", window_id, entry["name"]])
        tmux(["set", "-w", "-t", window_id, "automatic-rename", "off"])
        tmux(["set", "-w", "-t", window_id, "@llm-name", entry["name"]])
        tmux(["set", "-w", "-t", window_id, "@llm-key", entry["key"]])


def propose() -> dict[str, dict[str, str]]:
    """Release stale windows, ask codex for the rest, return the proposal."""
    windows = list_windows()
    to_name, to_release = plan(windows)
    for window in to_release:
        release(window)
    if not to_name:
        return {}
    pending = {w.id for w in to_name}
    taken = [w.topic or w.name for w in windows if w.id not in pending]
    topics = ask_codex(to_name, taken)
    return {
        w.id: {"old": w.name, "name": compose(w.icon, project_prefix(w.repo), topics[w.id]), "key": w.key}
        for w in to_name
        if w.id in topics
    }


def run(mode: str) -> int:
    if mode == "print":
        to_name, to_release = plan(list_windows())
        for window in to_release:
            print(f"{window.id} release {window.name!r}")
        for window in to_name:
            print(f"{window.id} name {json.dumps(describe(window), ensure_ascii=False)}")
        return 0

    if mode == "apply":
        if not PROPOSAL_PATH.exists():
            print("no proposal saved; run --propose first", file=sys.stderr)
            return 1
        apply(json.loads(PROPOSAL_PATH.read_text()))
        PROPOSAL_PATH.unlink()
        return 0

    proposal = propose()
    if mode == "propose":
        if not proposal:
            print("nothing to name")
            return 0
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        PROPOSAL_PATH.write_text(json.dumps(proposal, ensure_ascii=False, indent=1))
        for window_id, entry in proposal.items():
            print(f"{window_id:>5}  {entry['old']:<60} -> {entry['name']}")
        print(f"\nsaved to {PROPOSAL_PATH}; edit names there if needed, then run --apply")
        return 0

    apply(proposal)
    return 0


def run_daemon() -> int:
    if shutil.which("codex") is None:
        return 0  # nothing to poll for without the codex CLI
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_dir = STATE_DIR / ".daemon.lock"
    script_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    try:
        lock_dir.mkdir()
    except FileExistsError:
        try:
            pid = int((lock_dir / "pid").read_text().strip())
            os.kill(pid, 0)
            if (lock_dir / "script-sha256").read_text().strip() == script_digest:
                return 0
            os.kill(pid, 15)
            time.sleep(0.2)
        except (FileNotFoundError, ProcessLookupError, ValueError, PermissionError):
            pass
        shutil.rmtree(lock_dir, ignore_errors=True)
        try:
            lock_dir.mkdir()
        except FileExistsError:
            return 0

    (lock_dir / "pid").write_text(f"{os.getpid()}\n")
    (lock_dir / "script-sha256").write_text(f"{script_digest}\n")

    def owns_lock() -> bool:
        try:
            return (lock_dir / "pid").read_text().strip() == str(os.getpid())
        except OSError:
            return False
    try:
        while True:
            if not owns_lock():
                return 0  # another instance took over; leave its lock alone
            if subprocess.run(["tmux", "has-session"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
                return 0
            try:
                run("auto")
            except Exception:
                pass
            time.sleep(DAEMON_INTERVAL)
    finally:
        if owns_lock():
            shutil.rmtree(lock_dir, ignore_errors=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Name tmux windows with one batched codex call.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--daemon", action="store_true", help="poll and name automatically until tmux exits")
    group.add_argument("--print", action="store_const", const="print", dest="mode", help="show the plan without calling codex")
    group.add_argument("--propose", action="store_const", const="propose", dest="mode", help="ask codex and save names for review")
    group.add_argument("--apply", action="store_const", const="apply", dest="mode", help="apply the saved proposal")
    args = parser.parse_args(argv[1:])
    if args.daemon:
        return run_daemon()
    return run(args.mode or "auto")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
