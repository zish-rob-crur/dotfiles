#!/usr/bin/env python3
"""Give tmux windows short, meaningful names with one batched `codex exec` call.

Every pane of a window contributes its directory, command, git branch and
title; the model returns one topic per window and the script composes
"<icon> <prefix>:<topic>" from the existing @pane-window-icon and a project
prefix derived from the repository name. A window is renamed only when some
pane carries a signal beyond the directory (a branch or a title); windows the
user renamed by hand are never touched. Names set here are tracked in the
window options @llm-name and @llm-key so later runs can tell them apart from
manual renames and skip windows whose context has not changed.

task-board.py names new windows automatically as part of its own codex call
(see `plan`, `describe`, `finish`); this CLI is the manual path:

    window_namer.py            name changed windows now
    window_namer.py --print    show which windows would be sent, no codex call
    window_namer.py --propose  ask codex and print the names without applying
    window_namer.py --apply    apply the last proposal

Project prefix overrides live in ~/.config/task-board/config.toml:

    [prefixes]
    "my-long-repo-name" = "mono"

Every rename, release and codex call is appended to
$XDG_CACHE_HOME/tmux-window-namer/namer.log.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_batch  # noqa: E402
from tmux_panes import clean_command, clean_icon, clean_title, git_info, tmux  # noqa: E402

STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "tmux-window-namer"
PROPOSAL_PATH = STATE_DIR / "proposal.json"
LOG_PATH = STATE_DIR / "namer.log"
CONFIG_PATH = Path(os.environ.get("TASK_BOARD_CONFIG", str(Path.home() / ".config/task-board/config.toml")))
MAX_NAME_LENGTH = 16  # only the current window shows its name on the rail, so this can be generous
FIELD_SEP = "\x1f"

log = logging.getLogger("window-namer")

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


def prefix_overrides() -> dict[str, str]:
    """Repo -> prefix pairs from the [prefixes] table of the task-board config."""
    try:
        table = tomllib.loads(CONFIG_PATH.read_text()).get("prefixes", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {str(k): str(v) for k, v in table.items()}


def project_prefix(repo: str) -> str:
    if not repo:
        return ""
    override = prefix_overrides().get(repo)
    if override:
        return override
    words = [w for w in re.split(r"[-_]+", repo.lower()) if w]
    if len(words) >= 2:
        return "".join(w[0] for w in words)[:4]
    return words[0][:4] if words else ""


def compose(icon: str, prefix: str, topic: str) -> str:
    body = f"{prefix}:{topic}" if prefix else topic
    return f"{icon} {body}" if icon else body


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
    git_info.cache_clear()  # branches may change between rounds
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
        pane.repo, pane.branch = git_info(pane.path)
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
    prompt = PROMPT.format(
        max_len=MAX_NAME_LENGTH,
        taken=", ".join(sorted(taken)) or "(none)",
        windows=json.dumps([describe(w) for w in to_name], ensure_ascii=False, indent=1),
    )
    reply = codex_batch.ask(prompt, OUTPUT_SCHEMA, state_dir=STATE_DIR, log=log,
                            label=f"naming {[w.id for w in to_name]} taken={sorted(taken)}")
    names = {}
    for entry in (reply or {}).get("names", []):
        name = sanitize(str(entry.get("name", "")))
        if name:
            names[str(entry.get("id", ""))] = name
    return names


def release(window: Window) -> None:
    log.info("release %s %r (no branch or title left)", window.id, window.name)
    tmux(["set", "-w", "-t", window.id, "-u", "@llm-name"])
    tmux(["set", "-w", "-t", window.id, "-u", "@llm-key"])
    tmux(["set", "-w", "-t", window.id, "automatic-rename", "on"])


def apply(proposal: dict[str, dict]) -> None:
    """proposal maps window id -> {"old": ..., "name": ..., "key": ..., "context": ...}."""
    for window_id, entry in proposal.items():
        log.info("rename %s %r -> %r context=%s", window_id, entry.get("old"), entry["name"], json.dumps(entry.get("context"), ensure_ascii=False))
        tmux(["rename-window", "-t", window_id, entry["name"]])
        tmux(["set", "-w", "-t", window_id, "automatic-rename", "off"])
        tmux(["set", "-w", "-t", window_id, "@llm-name", entry["name"]])
        tmux(["set", "-w", "-t", window_id, "@llm-key", entry["key"]])


def finish(window: Window, topic: str) -> dict:
    """Proposal entry for a window once the model has chosen its topic."""
    return {"old": window.name, "name": compose(window.icon, project_prefix(window.repo), sanitize(topic)), "key": window.key, "context": describe(window)}


def propose() -> dict[str, dict]:
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
    return {w.id: finish(w, topics[w.id]) for w in to_name if w.id in topics}


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


def setup_logging() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not log.handlers:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Name tmux windows with one batched codex call.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--print", action="store_const", const="print", dest="mode", help="show the plan without calling codex")
    group.add_argument("--propose", action="store_const", const="propose", dest="mode", help="ask codex and save names for review")
    group.add_argument("--apply", action="store_const", const="apply", dest="mode", help="apply the saved proposal")
    args = parser.parse_args(argv[1:])
    setup_logging()
    return run(args.mode or "auto")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
