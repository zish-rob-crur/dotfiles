#!/usr/bin/env python3

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "codex-tmux-status"
CLAUDE_STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "claude-tmux-status"
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def tmux(args: list[str]) -> str:
    return subprocess.check_output(["tmux", *args], text=True, stderr=subprocess.DEVNULL).strip()


def shell_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def command_name(command: str) -> str:
    words = shell_words(command)
    if not words:
        return ""
    return Path(words[0]).name


def assistant_tool(command: str) -> str:
    name = command_name(command)
    if name == "codex" or name.startswith("codex-"):
        return "codex"
    if name == "claude" or name.startswith("claude-"):
        return "claude"
    return ""


def is_assistant_command(command: str) -> bool:
    return bool(assistant_tool(command))


def has_resume_id(command: str, tool: str) -> bool:
    words = shell_words(command)
    if tool == "claude":
        for index, word in enumerate(words):
            if word in {"--resume", "-r"} and index + 1 < len(words) and UUID_RE.match(words[index + 1]):
                return True
            if word.startswith("--resume=") and UUID_RE.match(word.split("=", 1)[1]):
                return True
    for index, word in enumerate(words[:-1]):
        if word == "resume" and UUID_RE.match(words[index + 1]):
            return True
    return False


def state_path_for_pane(pane_id: str, tool: str) -> Path:
    state_dir = CLAUDE_STATE_DIR if tool == "claude" else STATE_DIR
    return state_dir / f"pane-{pane_id.lstrip('%')}.json"


def load_pane_state(pane_id: str, tool: str) -> dict[str, object]:
    path = state_path_for_pane(pane_id, tool)
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def parse_iso_epoch(value: object) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def process_lstart_epoch(pid: str) -> Optional[float]:
    try:
        text = subprocess.check_output(
            ["ps", "-p", pid, "-o", "lstart="],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    try:
        return datetime.strptime(text, "%a %b %d %H:%M:%S %Y").timestamp()
    except ValueError:
        return None


def direct_assistant_child_start_epoch(pane_pid: str, tool: str) -> Optional[float]:
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    starts: list[float] = []
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid, ppid, command = parts
        if ppid == pane_pid and assistant_tool(command) == tool:
            start = process_lstart_epoch(pid)
            if start is not None:
                starts.append(start)
    return min(starts) if starts else None


def tmux_target(session_name: str, window_number: str, pane_index: str) -> str:
    return f"{session_name}:{window_number}.{pane_index}"


def pane_id_for_target(target: str) -> str:
    return tmux(["display-message", "-p", "-t", target, "#{pane_id}"])


def pane_pid_for_target(target: str) -> str:
    return tmux(["display-message", "-p", "-t", target, "#{pane_pid}"])


def unescape_saved_dir(value: str) -> str:
    if value.startswith(":"):
        value = value[1:]
    return value.replace("\\ ", " ")


def state_matches_pane(
    state: dict[str, object],
    tool: str,
    pane_id: str,
    session_name: str,
    saved_dir: str,
    process_start_epoch: Optional[float],
) -> bool:
    id_key = "session_id" if tool == "claude" else "thread_id"
    session_id = str(state.get(id_key, "")).strip()
    if not UUID_RE.match(session_id):
        return False
    if str(state.get("tool", tool)).strip() not in {"", tool}:
        return False
    if str(state.get("pane_id", "")).strip() != pane_id:
        return False
    if str(state.get("session_name", "")).strip() != session_name:
        return False

    state_cwd = str(state.get("cwd", "")).strip()
    if state_cwd and saved_dir:
        if os.path.realpath(state_cwd) != os.path.realpath(saved_dir):
            return False

    completed_epoch = parse_iso_epoch(state.get("completed_at")) or parse_iso_epoch(state.get("updated_at"))
    if completed_epoch is not None and process_start_epoch is not None:
        if completed_epoch + 5 < process_start_epoch:
            return False

    return True


def state_cwd(state: dict[str, object]) -> str:
    return str(state.get("cwd", "")).strip()


def restore_command(tool: str, session_id: str, saved_dir: str, state: dict[str, object]) -> str:
    if tool == "claude":
        command = f"claude --resume {session_id}"
        cwd = state_cwd(state)
        if cwd and saved_dir and os.path.realpath(cwd) != os.path.realpath(saved_dir):
            return f"cd {shlex.quote(cwd)} && {command}"
        return command
    return f"codex resume {session_id}"


def rewrite_pane_line(parts: list[str]) -> Optional[str]:
    if len(parts) < 11 or parts[0] != "pane":
        return None

    session_name = parts[1]
    window_number = parts[2]
    pane_index = parts[5]
    saved_dir = unescape_saved_dir(parts[7])
    pane_current_command = parts[9]
    saved_command_field = parts[10]
    saved_command = saved_command_field[1:] if saved_command_field.startswith(":") else saved_command_field
    tool = assistant_tool(pane_current_command) or assistant_tool(saved_command)

    if not tool:
        return None
    if has_resume_id(saved_command, tool):
        return None

    target = tmux_target(session_name, window_number, pane_index)
    try:
        pane_id = pane_id_for_target(target)
        pane_pid = pane_pid_for_target(target)
    except (OSError, subprocess.CalledProcessError):
        return None

    state = load_pane_state(pane_id, tool)
    process_start_epoch = direct_assistant_child_start_epoch(pane_pid, tool)
    if not state_matches_pane(state, tool, pane_id, session_name, saved_dir, process_start_epoch):
        return None

    id_key = "session_id" if tool == "claude" else "thread_id"
    session_id = str(state[id_key]).strip()
    parts[10] = f":{restore_command(tool, session_id, saved_dir, state)}"
    return "\t".join(parts)


def rewrite_file(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    changed = False
    rewritten: list[str] = []
    for line in lines:
        parts = line.split("\t")
        replacement = rewrite_pane_line(parts)
        if replacement is None:
            rewritten.append(line)
        else:
            rewritten.append(replacement)
            changed = True

    if changed:
        path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        return 0
    rewrite_file(Path(sys.argv[1]).expanduser())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
