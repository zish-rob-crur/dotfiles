#!/usr/bin/env python3
"""Entries for fzf-goto.sh (prefix+g / prefix+w): the current session as a tree.

One line per window (index, assistant state, name, directory, git branch)
followed by its
panes worth telling apart: assistants, editors and anything with a title. Plain
idle shells are reached through their window. A window with one such pane
carries that pane's text on its own line. A pane's text is the task board's summary for it when there
is one ("what it is doing -> what happens next"), else its cleaned title, so
typing a topic finds the pane working on it.

Each output line is tab-separated for the shell script:
    type id display state window_id pane_id target_session target_client target_tty window_index pane_index
with "-" for empty fields (bash `read` collapses empty tab-separated fields).
state is CURRENT, VISIBLE (shown by another Ghostty client of the same session
group; the target_* fields name that client) or NORMAL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tmux_panes import clean_command, clean_title, tmux  # noqa: E402

FS = "\x1f"
BOARD = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "tmux-task-board" / "board.json"
DIM, RESET, CYAN = "\033[2m", "\033[0m", "\033[36m"
SHELLS = {"zsh", "bash", "fish", "sh"}
STATE_GLYPHS = (("●", "\033[34m●"), ("◆", "\033[33m◆"), ("󰄬", "\033[32m✓"), ("×", "\033[31m×"))


def rows(args: list[str], width: int) -> list[list[str]]:
    return [parts for line in tmux(args).splitlines() if len(parts := line.split(FS)) == width]


def glyph(badge: str) -> str:
    return next((f"{shown}{RESET}" for raw, shown in STATE_GLYPHS if raw in badge), " ")


def load_board() -> dict:
    """The task board's last snapshot (refreshed every 10 s by its daemon). Reading
    branches from it keeps the popup fast: no git process per directory."""
    try:
        return json.loads(BOARD.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def branch_label(branch: str, directory: str) -> str:
    """Branch to show next to a directory, or "" when the worktree directory
    already spells it ("repo.fix-agentic-x" on "fix/agentic/x")."""
    if not branch or re.sub(r"[^\w]+", "-", branch).strip("-") in re.sub(r"[^\w]+", "-", directory):
        return ""
    return branch


def pane_tasks(board: dict) -> dict[str, str]:
    """pane id -> "summary → next" from the task board."""
    tasks = {}
    for window in board.get("windows", []):
        for task in window.get("tasks", []):
            text = task.get("summary", "") + (f" {DIM}→ {task['next']}{RESET}" if task.get("next") else "")
            for pane_id in task.get("panes", []):
                tasks[pane_id] = text
    return tasks


def pane_text(command: str, title: str, session_title: str, path: str, task: str) -> str:
    if task:
        return task
    title = clean_title(session_title or title, path)
    title = re.sub(r"(\s+-)?\s+\(.*\)\s+-\s+N?vim$|\s+-\s+N?vim$", "", title).lstrip("<")  # "file.md (~/dir) - Nvim" -> "file.md"
    return "" if title == command or title in path.split("/") else title


def visible_clients(current_client: str, session: str, group: str) -> dict[str, tuple[str, str, str]]:
    """window id -> (session, client, tty) for other Ghostty clients in the same session group."""
    seen = {}
    for client, tty, term, termtype, csession, cgroup, window_id in rows(
        ["list-clients", "-F", FS.join(["#{client_name}", "#{client_tty}", "#{client_termname}", "#{client_termtype}",
                                        "#{session_name}", "#{session_group}", "#{window_id}"])], 7):
        same_group = cgroup == group if cgroup else csession == session
        if client != current_client and same_group and "ghostty" in f"{term} {termtype}".lower():
            seen.setdefault(window_id, (csession, client, tty))
    return seen


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    for name in ("--client", "--session", "--group", "--window-id"):
        parser.add_argument(name, default="")
    args = parser.parse_args(argv[1:])
    group = args.group or args.session
    visible = visible_clients(args.client, args.session, group)
    board = load_board()
    tasks = pane_tasks(board)
    branches = {w.get("id"): w.get("branch", "") for w in board.get("windows", [])}

    panes: dict[str, list[list[str]]] = {}
    for row in rows(["list-panes", "-s", "-t", args.session, "-F", FS.join(
            ["#{window_id}", "#{pane_id}", "#{pane_index}", "#{pane_current_command}", "#{pane_title}",
             "#{@codex-session-title}", "#{pane_current_path}"])], 7):
        panes.setdefault(row[0], []).append(row[1:])

    out = []

    def emit(kind, id_, display, window_id, pane_id, window_index, pane_index):
        if window_id == args.window_id:
            state, target = "CURRENT", (args.session, args.client, "")
        elif window_id in visible:
            state, target = "VISIBLE", visible[window_id]
        else:
            state, target = "NORMAL", (args.session, args.client, "")
        fields = [kind, id_, display, state, window_id, pane_id, *target, window_index, pane_index]
        out.append("\t".join(f.replace("\t", " ") or "-" for f in fields))

    for window_id, index, name, badge, active_pane, path in rows(["list-windows", "-t", args.session, "-F", FS.join(
            ["#{window_id}", "#{window_index}", "#{window_name}", "#{@codex-badge}", "#{pane_id}", "#{pane_current_path}"])], 6):
        mark = "*" if window_id == args.window_id else " "
        where = f"  {CYAN}⧉ {visible[window_id][2] or visible[window_id][1]}{RESET}" if window_id in visible else ""
        directory = os.path.basename(path)
        branch = branch_label(branches.get(window_id, ""), directory)
        branch = f" {DIM}⎇ {branch}{RESET}" if branch else ""
        head = f"{index:>2}{mark} {glyph(badge)} {name}  {DIM}{directory}{RESET}{branch}{where}"
        members = []
        for pid, pindex, cmd, title, stitle, ppath in panes.get(window_id, []):
            cmd = clean_command(cmd)
            text = pane_text(cmd, title, stitle, ppath, tasks.get(pid, ""))
            if text or cmd not in SHELLS:
                members.append((pid, pindex, cmd, ppath, text))
        if len(members) <= 1:
            text = f"  {members[0][4]}" if members else ""
            emit("W", window_id, f"{head}{text}".rstrip(), window_id, members[0][0] if members else active_pane, index, "")
            continue
        emit("W", window_id, head, window_id, active_pane, index, "")
        for n, (pid, pindex, cmd, ppath, text) in enumerate(members):
            branch = "└" if n == len(members) - 1 else "├"
            elsewhere = f"  {DIM}{os.path.basename(ppath)}{RESET}" if ppath != path else ""
            emit("P", pid, f"     {DIM}{branch}{RESET} {cmd:<7} {text}{elsewhere}".rstrip(),
                 window_id, pid, index, pindex)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
