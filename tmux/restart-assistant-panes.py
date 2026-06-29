#!/usr/bin/env python3

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "codex-tmux-status"
CLAUDE_STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "claude-tmux-status"
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
SPINNER_PREFIXES = tuple(f"{char} " for char in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⠐")


def tmux(args: list[str], check: bool = True) -> str:
    result = subprocess.run(
        ["tmux", *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout.rstrip("\n")


def command_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def command_name(command: str) -> str:
    words = command_words(command)
    if not words:
        return ""
    return Path(words[0]).name


def command_tool(command: str) -> str:
    name = command_name(command)
    if name == "codex" or name.startswith("codex-"):
        return "codex"
    if name == "claude" or name.startswith("claude-"):
        return "claude"
    return ""


def is_version_name(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 3 and all(part.isdigit() for part in parts)


def load_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def state_path(pane_id: str, tool: str) -> Path:
    root = CLAUDE_STATE_DIR if tool == "claude" else STATE_DIR
    return root / f"pane-{pane_id.lstrip('%')}.json"


def realpath(value: str) -> str:
    return os.path.realpath(os.path.expanduser(value)) if value else ""


def usable_dir(value: str) -> str:
    path = Path(value).expanduser() if value else Path.home()
    return str(path) if path.is_dir() else str(Path.home())


def state_matches_pane(state: dict[str, object], pane: dict[str, str]) -> bool:
    expected = {
        "pane_id": pane["pane_id"],
        "session_name": pane["session_name"],
        "window_id": pane["window_id"],
    }
    for key, value in expected.items():
        state_value = str(state.get(key, "")).strip()
        if state_value and state_value != value:
            return False

    state_cwd = str(state.get("cwd", "")).strip()
    if state_cwd and pane["path"] and realpath(state_cwd) != realpath(pane["path"]):
        return False

    return True


def uuid_from_state(state: dict[str, object], keys: list[str]) -> str:
    for key in keys:
        value = str(state.get(key, "")).strip()
        match = UUID_RE.search(value)
        if match:
            return match.group(0)
    return ""


def saved_session_id(tool: str, pane: dict[str, str]) -> tuple[str, str]:
    if tool == "claude":
        state = load_json(state_path(pane["pane_id"], "claude"))
        if state_matches_pane(state, pane):
            session_id = uuid_from_state(state, ["session_id"])
            if session_id:
                return session_id, str(state.get("cwd", "")).strip()

        badge_state = load_json(state_path(pane["pane_id"], "codex"))
        if state_matches_pane(badge_state, pane) and str(badge_state.get("source", "")).strip() == "claude":
            session_id = uuid_from_state(badge_state, ["session_id", "thread_id", "summary"])
            if session_id:
                return session_id, str(badge_state.get("cwd", "")).strip()
        return "", ""

    state = load_json(state_path(pane["pane_id"], "codex"))
    if state_matches_pane(state, pane):
        thread_id = uuid_from_state(state, ["thread_id", "session_id", "summary"])
        if thread_id:
            return thread_id, str(state.get("cwd", "")).strip()
    return "", ""


def children_by_parent() -> dict[str, list[str]]:
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return {}

    children: dict[str, list[str]] = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        _pid, ppid, command = parts
        children.setdefault(ppid, []).append(command)
    return children


def child_command_for_tool(pane_pid: str, children: dict[str, list[str]]) -> tuple[str, str]:
    for command in children.get(pane_pid, []):
        tool = command_tool(command)
        if tool:
            return tool, command
    return "", ""


def pane_tool(pane: dict[str, str], child_tool: str) -> str:
    if child_tool:
        return child_tool

    tool = command_tool(pane["command"])
    if tool:
        return tool

    if "/claude-envs/" in pane["path"]:
        return "claude"

    if is_version_name(pane["command"]) and (pane["title"].startswith("✳ ") or pane["title"].startswith(SPINNER_PREFIXES)):
        return "claude"

    return ""


def resume_id_from_command(tool: str, command: str) -> str:
    words = command_words(command)
    if tool == "claude":
        for index, word in enumerate(words):
            if word in {"--resume", "-r"} and index + 1 < len(words):
                match = UUID_RE.fullmatch(words[index + 1])
                if match:
                    return match.group(0)
            if word.startswith("--resume="):
                match = UUID_RE.fullmatch(word.split("=", 1)[1])
                if match:
                    return match.group(0)
        return ""

    for index, word in enumerate(words[:-1]):
        if word == "resume":
            match = UUID_RE.fullmatch(words[index + 1])
            if match:
                return match.group(0)
    return ""


def capture_resume_id(tool: str, pane_id: str) -> str:
    try:
        text = tmux(["capture-pane", "-p", "-J", "-t", pane_id, "-S", "-2000"], check=True)
    except subprocess.CalledProcessError:
        return ""

    patterns = [
        re.compile(r"\bcodex\s+resume\s+(" + UUID_RE.pattern.strip(r"\b") + r")\b"),
        re.compile(r"\bclaude\s+(?:--resume|-r)\s+(" + UUID_RE.pattern.strip(r"\b") + r")\b"),
        re.compile(r"\b--resume[= ](" + UUID_RE.pattern.strip(r"\b") + r")\b"),
    ]
    if tool == "codex":
        patterns = patterns[:1]
    elif tool == "claude":
        patterns = patterns[1:]

    for pattern in patterns:
        matches = pattern.findall(text)
        if matches:
            return matches[-1]
    return ""


def codex_resume_words(command: str, session_id: str) -> list[str]:
    words = command_words(command)
    if not words or not command_tool(command):
        words = ["codex"]

    cleaned: list[str] = []
    index = 0
    while index < len(words):
        if words[index] == "resume" and index + 1 < len(words) and UUID_RE.fullmatch(words[index + 1]):
            index += 2
            continue
        cleaned.append(words[index])
        index += 1
    return cleaned + ["resume", session_id]


def claude_resume_words(command: str, session_id: str) -> list[str]:
    words = command_words(command)
    if not words or not command_tool(command):
        words = ["claude"]

    cleaned: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word in {"--resume", "-r"}:
            index += 2 if index + 1 < len(words) else 1
            continue
        if word.startswith("--resume="):
            index += 1
            continue
        cleaned.append(word)
        index += 1
    return cleaned + ["--resume", session_id]


def resume_words(tool: str, child_command: str, session_id: str) -> list[str]:
    if tool == "claude":
        return claude_resume_words(child_command, session_id)
    return codex_resume_words(child_command, session_id)


def shell_command(words: list[str], cwd: str, tool: str) -> str:
    shell = os.environ.get("SHELL", "") or "/bin/zsh"
    if not Path(shell).exists():
        shell = "/bin/zsh"

    command = " ".join(shlex.quote(word) for word in words)
    workdir = usable_dir(cwd)
    inner = (
        f"cd {shlex.quote(workdir)} 2>/dev/null || cd; "
        f"{command}; "
        "status=$?; "
        f"printf '\\n[{tool} exited with status %s; shell refreshed]\\n' \"$status\"; "
        'exec "${SHELL:-/bin/zsh}" -l'
    )
    return f"exec {shlex.quote(shell)} -lic {shlex.quote(inner)}"


def list_panes() -> list[dict[str, str]]:
    fmt = "\t".join(
        [
            "#{pane_id}",
            "#{pane_pid}",
            "#{pane_current_command}",
            "#{pane_title}",
            "#{pane_current_path}",
            "#{session_name}",
            "#{window_id}",
            "#{window_index}",
            "#{pane_index}",
            "#{window_name}",
        ]
    )
    output = tmux(["list-panes", "-a", "-F", fmt], check=True)
    panes: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        parts = line.split("\t", 9)
        if len(parts) != 10:
            continue
        pane_id, pane_pid, command, title, path, session_name, window_id, window_index, pane_index, window_name = parts
        if not pane_id or pane_id in seen:
            continue
        seen.add(pane_id)
        panes.append(
            {
                "pane_id": pane_id,
                "pane_pid": pane_pid,
                "command": command,
                "title": title,
                "path": path,
                "session_name": session_name,
                "window_id": window_id,
                "window_index": window_index,
                "pane_index": pane_index,
                "window_name": window_name,
            }
        )
    return panes


def restart_panes(dry_run: bool, tool_filter: str) -> tuple[int, int, int]:
    children = children_by_parent()
    restarted = 0
    skipped = 0
    found = 0

    for pane in list_panes():
        child_tool, child_command = child_command_for_tool(pane["pane_pid"], children)
        tool = pane_tool(pane, child_tool)
        if not tool or (tool_filter != "all" and tool != tool_filter):
            continue

        found += 1
        session_id = resume_id_from_command(tool, child_command)
        cwd = ""
        if not session_id:
            session_id, cwd = saved_session_id(tool, pane)
        if not session_id:
            session_id = capture_resume_id(tool, pane["pane_id"])

        if not session_id:
            skipped += 1
            print(f"skip {pane['pane_id']} {tool}: no saved resume id", file=sys.stderr)
            continue

        cwd = cwd or pane["path"]
        words = resume_words(tool, child_command, session_id)
        command = shell_command(words, cwd, tool)
        label = f"{pane['session_name']}:{pane['window_index']}.{pane['pane_index']} {tool}"

        if dry_run:
            print(f"would restart {label} ({pane['pane_id']})")
            continue

        try:
            tmux(["respawn-pane", "-k", "-t", pane["pane_id"], "-c", usable_dir(cwd), command])
        except subprocess.CalledProcessError:
            skipped += 1
            print(f"skip {pane['pane_id']} {tool}: respawn failed", file=sys.stderr)
            continue

        restarted += 1
        print(f"restarted {label} ({pane['pane_id']})")

    if not dry_run:
        refresh = Path.home() / "GitHubRepos/dotfiles/tmux/codex-window-badges-refresh.sh"
        if refresh.exists():
            subprocess.run([str(refresh), "--force"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    return found, restarted, skipped


def display_message(text: str) -> None:
    subprocess.run(["tmux", "display-message", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restart Codex and Claude Code panes with saved resume ids.")
    parser.add_argument("--dry-run", action="store_true", help="show what would restart without touching panes")
    parser.add_argument("--tool", choices=["all", "codex", "claude"], default="all")
    args = parser.parse_args()

    try:
        found, restarted, skipped = restart_panes(args.dry_run, args.tool)
    except subprocess.CalledProcessError:
        message = "No tmux server found"
        if not args.dry_run:
            display_message(message)
        print(message, file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"found={found} skipped={skipped}")
        return 0

    if found == 0:
        message = "No Codex/Claude panes found"
    elif restarted == 0:
        message = f"No Codex/Claude panes restarted ({skipped} skipped; no resume id or respawn failed)"
    else:
        message = f"Restarted {restarted} Codex/Claude pane(s)"
        if skipped:
            message += f"; skipped {skipped}"

    display_message(message)
    print(message)
    return 0 if restarted or found == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
