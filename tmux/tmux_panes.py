"""Shared tmux pane helpers for window_namer.py and task-board.py."""

from __future__ import annotations

import os
import re
import socket
import subprocess
from functools import lru_cache

SPINNERS = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def tmux(args: list[str]) -> str:
    return subprocess.run(["tmux", *args], capture_output=True, text=True).stdout


def clean_title(title: str, path: str = "") -> str:
    """Drop spinner glyphs and titles that carry no information beyond the directory."""
    title = re.sub(r"^[\s✳⠁-⣿]+", "", title).strip()
    if not title or title.endswith("...") or title == socket.gethostname():
        return ""
    if path and title in {os.path.basename(path), "~"}:
        return ""
    return title


def clean_command(command: str) -> str:
    """Claude Code shows up as its version number in pane_current_command."""
    return "claude" if re.fullmatch(r"\d+\.\d+\.\d+", command) else command


def clean_icon(icon: str) -> str:
    """Keep the leading tool icon from @pane-window-icon; drop layout suffixes like ×2 or │."""
    icon = re.split(r"[×│/\d]", icon, maxsplit=1)[0].strip()
    return "" if icon == "·" else icon


def tool_of(command: str, title: str) -> str:
    """'codex', 'claude' or '' for a pane."""
    if command.startswith("codex"):
        return "codex"
    if command.startswith("claude") or (re.fullmatch(r"\d+\.\d+\.\d+", command) and title.startswith(("✳",) + SPINNERS)):
        return "claude"
    return ""


@lru_cache(maxsize=None)
def git_info(path: str) -> tuple[str, str]:
    """(repo name, branch) for a path; empty strings outside a repository.
    Call git_info.cache_clear() at the start of a round: branches change."""
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "", ""
    lines = result.stdout.splitlines()
    if len(lines) != 2:
        return "", ""
    branch = "" if lines[0] == "HEAD" else lines[0]
    repo = os.path.basename(lines[1]).split(".", 1)[0]  # worktree dirs look like repo.branch
    return repo, branch


def user_idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input (macOS HID idle time); 0 if unknown."""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True, timeout=2).stdout
    except (subprocess.TimeoutExpired, OSError):
        return 0.0
    match = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(match.group(1)) / 1e9 if match else 0.0
