#!/usr/bin/env python3
"""Snapshot, acknowledge, and focus completed tmux agent panes for Hammerspoon HUD."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import assistant_completion_state as acs  # noqa: E402
import tmux_panes  # noqa: E402

DEFAULT_STATE_ROOT = (
    Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    / "codex-tmux-status"
)
DEFAULT_BOARD_PATH = (
    Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    / "tmux-task-board"
    / "board.json"
)
FOCUS_SCRIPT = HERE / "focus-ghostty-window.sh"
BADGES_REFRESH = HERE / "codex-window-badges-refresh.sh"

FS = "\x1f"
SOURCE_LABELS = {
    "codex": "Codex",
    "claude": "Claude",
    "agy": "Antigravity",
}
ANSI_ANY_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
SGR_OR_SEQ_RE = re.compile(
    r"\x1b\[([0-9;]*)m|\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
)
BOX_DIVIDER_RE = re.compile(r"^[\s─━═╌╍┄┅┈┉╭╮╰╯┌┐└┘├┤┬┴┼│┃\-_=]{6,}$")
UNICODE_DIVIDER_RE = re.compile(r"^[\s─━═╌╍┄┅┈┉╭╮╰╯┌┐└┘├┤┬┴┼│┃]{6,}$")
BOX_PROMPT_RE = re.compile(r"^[│┃]\s*[›❯>]?\s*[│┃]?$")
TUI_PROMPT_RE = re.compile(r"^(?:[›❯](?:\s.*)?|>\s*$)")
TUI_STATUS_RE = re.compile(
    r"(?:\b\d+%\s*(?:context\s+)?left\b|\bcontext left\b|\bweekly\s+\d+%\s+left\b|"
    r"\? for shortcuts|\bbypass permissions\b|shift\+tab to cycle|"
    r"\besc to interrupt\b|\bPress enter to\b|\bauto-compact\b)",
    re.IGNORECASE,
)
TUI_STATUS_CONT_RE = re.compile(r"(?:·\s*(?:~/|/|Context\s*$))", re.IGNORECASE)

# Standard ANSI 16 colors harmonized with Everforest Dark (#1e2326 / #272e33).
ANSI_FG_COLORS = {
    30: "#4b565c", 31: "#e67e80", 32: "#a7c080", 33: "#dbbc7f",
    34: "#7fbbb3", 35: "#d699b6", 36: "#83c092", 37: "#d3c6aa",
    90: "#7a8478", 91: "#e67e80", 92: "#a7c080", 93: "#dbbc7f",
    94: "#7fbbb3", 95: "#d699b6", 96: "#83c092", 97: "#ece4d1",
}
ANSI_BG_COLORS = {
    40: "#272e33", 41: "#4a3336", 42: "#34412f", 43: "#4a4330",
    44: "#2f4145", 45: "#463544", 46: "#2d4039", 47: "#3d484d",
    100: "#384249", 101: "#593b3f", 102: "#3f5038", 103: "#595038",
    104: "#384e54", 105: "#523d50", 106: "#354d44", 107: "#4f585e",
}
CUBE_STEPS = (0, 95, 135, 175, 215, 255)


def strip_ansi(text: str) -> str:
    return ANSI_ANY_RE.sub("", text)


def xterm256_hex(index: int) -> str:
    if index < 8:
        return ANSI_FG_COLORS[30 + index]
    if index < 16:
        return ANSI_FG_COLORS[90 + index - 8]
    if index < 232:
        n = index - 16
        b = CUBE_STEPS[n % 6]
        g = CUBE_STEPS[(n // 6) % 6]
        r = CUBE_STEPS[n // 36]
        return f"#{r:02x}{g:02x}{b:02x}"
    level = 8 + (index - 232) * 10
    return f"#{level:02x}{level:02x}{level:02x}"


def trim_tui_footer(raw_text: str) -> list[str]:
    """Strip bottom TUI composer boxes, idle prompts, and status bars."""
    lines = [re.sub(r"[ \t]+$", "", ln) for ln in raw_text.splitlines()]
    while lines and not strip_ansi(lines[-1]).strip():
        lines.pop()
    if not lines:
        return []
    fallback = list(lines)

    floor = max(0, len(lines) - 16)
    found_chrome = False
    while len(lines) > floor:
        stripped = strip_ansi(lines[-1]).strip()
        if not stripped:
            lines.pop()
        elif TUI_STATUS_RE.search(stripped) or BOX_PROMPT_RE.fullmatch(stripped):
            lines.pop()
            found_chrome = True
        elif found_chrome and TUI_STATUS_CONT_RE.search(stripped) and len(stripped) <= 120:
            lines.pop()
        elif TUI_PROMPT_RE.match(stripped) and len(stripped) <= 100:
            lines.pop()
            found_chrome = True
        elif (
            found_chrome
            and re.match(r"^>\s+\S.*$", stripped)
            and len(stripped) <= 100
            and len(lines) >= 2
            and UNICODE_DIVIDER_RE.fullmatch(strip_ansi(lines[-2]).strip())
        ):
            lines.pop()
            found_chrome = True
        elif BOX_DIVIDER_RE.fullmatch(stripped) and (
            found_chrome or UNICODE_DIVIDER_RE.fullmatch(stripped)
        ):
            lines.pop()
            found_chrome = True
        else:
            break

    while lines and not strip_ansi(lines[-1]).strip():
        lines.pop()
    return lines if lines else fallback


def _style_css(style: dict[str, Any]) -> str:
    parts: list[str] = []
    if style["fg"]:
        parts.append(f"color:{style['fg']}")
    if style["bg"]:
        parts.append(f"background:{style['bg']}")
    if style["bold"]:
        parts.append("font-weight:600")
    if style["dim"]:
        parts.append("opacity:.72")
    if style["italic"]:
        parts.append("font-style:italic")
    if style["underline"]:
        parts.append("text-decoration:underline")
    return ";".join(parts)


def _apply_sgr(params_raw: str, style: dict[str, Any]) -> None:
    codes = [int(p) if p else 0 for p in params_raw.split(";")] if params_raw else [0]
    i = 0
    while i < len(codes):
        code = codes[i]
        if code == 0:
            style.update(fg=None, bg=None, bold=False, dim=False, italic=False, underline=False)
        elif code == 1:
            style["bold"] = True
        elif code == 2:
            style["dim"] = True
        elif code == 3:
            style["italic"] = True
        elif code == 4:
            style["underline"] = True
        elif code == 22:
            style["bold"] = False
            style["dim"] = False
        elif code == 23:
            style["italic"] = False
        elif code == 24:
            style["underline"] = False
        elif code == 39:
            style["fg"] = None
        elif code == 49:
            style["bg"] = None
        elif code in ANSI_FG_COLORS:
            style["fg"] = ANSI_FG_COLORS[code]
        elif code in ANSI_BG_COLORS:
            style["bg"] = ANSI_BG_COLORS[code]
        elif code in (38, 48) and i + 1 < len(codes):
            key = "fg" if code == 38 else "bg"
            mode = codes[i + 1]
            if mode == 5 and i + 2 < len(codes):
                style[key] = xterm256_hex(max(0, min(255, codes[i + 2])))
                i += 2
            elif mode == 2 and i + 4 < len(codes):
                r, g, b = (max(0, min(255, codes[i + 2 + k])) for k in range(3))
                style[key] = f"#{r:02x}{g:02x}{b:02x}"
                i += 4
        i += 1


def ansi_to_html(lines: list[str]) -> str:
    """Convert ANSI SGR lines into safe HTML spans."""
    style: dict[str, Any] = {
        "fg": None, "bg": None, "bold": False, "dim": False, "italic": False, "underline": False
    }
    out_lines: list[str] = []
    for line in lines:
        parts: list[str] = []
        pos = 0
        for match in SGR_OR_SEQ_RE.finditer(line):
            if match.start() > pos:
                chunk = html.escape(line[pos:match.start()], quote=False)
                css = _style_css(style)
                parts.append(f'<span style="{css}">{chunk}</span>' if css else chunk)
            if match.group(1) is not None:
                _apply_sgr(match.group(1), style)
            pos = match.end()
        if pos < len(line):
            chunk = html.escape(line[pos:], quote=False)
            css = _style_css(style)
            parts.append(f'<span style="{css}">{chunk}</span>' if css else chunk)
        out_lines.append("".join(parts))
    return "\n".join(out_lines)


def relative_age(completed_at_ns: int, now_ns: int | None = None) -> str:
    if completed_at_ns <= 0:
        return ""
    now = time.time_ns() if now_ns is None else now_ns
    diff = max(0, (now - completed_at_ns) // 1_000_000_000)
    if diff < 45:
        return "just now"
    if diff < 3600:
        return f"{max(1, diff // 60)}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"


def run_tmux(tmux_socket: str, *args: str, timeout: float = 1.5) -> str:
    cmd = ["tmux"]
    if tmux_socket:
        cmd.extend(["-S", tmux_socket])
    cmd.extend(args)
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def discover_live_servers(state_root: Path) -> list[tuple[str, Path]]:
    candidates: list[str] = []
    servers_dir = state_root / "servers"
    if servers_dir.is_dir():
        for server_json in sorted(servers_dir.glob("*/server.json")):
            data = acs.read_json(server_json)
            sock = str(data.get("tmux_socket", "")).strip()
            if sock:
                candidates.append(sock)
    fallback = run_tmux("", "display-message", "-p", "#{socket_path}", timeout=0.5).strip()
    if fallback:
        candidates.append(fallback)

    live: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for raw in candidates:
        sock = acs.canonical_socket(raw)
        if not sock or sock in seen:
            continue
        seen.add(sock)
        if acs.query_tmux_server_identity(sock) is None:
            continue
        sdir = acs.server_state_dir(state_root, sock)
        if sdir is not None and sdir.is_dir():
            live.append((sock, sdir))
    return live


def list_live_panes(tmux_socket: str) -> tuple[dict[tuple[str, str], dict[str, str]], dict[str, dict[str, str]]]:
    fmt = FS.join([
        "#{session_name}", "#{session_group}", "#{window_id}", "#{window_index}",
        "#{window_name}", "#{pane_id}", "#{pane_current_command}",
        "#{pane_title}", "#{pane_current_path}", "#{@agy-state}",
    ])
    raw = run_tmux(tmux_socket, "list-panes", "-a", "-F", fmt, timeout=1.0)
    by_session_pane: dict[tuple[str, str], dict[str, str]] = {}
    by_pane: dict[str, dict[str, str]] = {}
    for line in raw.splitlines():
        parts = line.split(FS)
        if len(parts) != 10:
            continue
        info = {
            "session_name": parts[0],
            "session_group": parts[1],
            "window_id": parts[2],
            "window_index": parts[3],
            "window_name": parts[4],
            "pane_id": parts[5],
            "command": parts[6],
            "title": parts[7],
            "path": parts[8],
            "agy_state": parts[9],
        }
        by_session_pane[(parts[0], parts[5])] = info
        by_pane.setdefault(parts[5], info)
    return by_session_pane, by_pane


def load_board_meta(board_path: Path) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    pane_map: dict[str, dict[str, str]] = {}
    win_map: dict[str, dict[str, str]] = {}
    data = acs.read_json(board_path)
    for win in data.get("windows", []) if isinstance(data.get("windows"), list) else []:
        if not isinstance(win, dict):
            continue
        wid = str(win.get("id", ""))
        if wid:
            win_map[wid] = {
                "repo": str(win.get("repo", "") or ""),
                "branch": str(win.get("branch", "") or ""),
            }
        for task in win.get("tasks", []) if isinstance(win.get("tasks"), list) else []:
            if not isinstance(task, dict):
                continue
            summary = str(task.get("summary", "") or "")
            nxt = str(task.get("next", "") or "")
            text = f"{summary} -> {nxt}" if (summary and nxt) else summary
            for pid in task.get("panes", []) if isinstance(task.get("panes"), list) else []:
                if isinstance(pid, str) and pid and text:
                    pane_map[pid] = {"summary": text, "next": nxt}
    return pane_map, win_map


def is_actively_working(pane_info: dict[str, str]) -> bool:
    if pane_info.get("agy_state") == "working":
        return True
    title = pane_info.get("title", "")
    return bool(title and title.startswith(tmux_panes.SPINNERS))


def _record_priority(item: dict[str, Any]) -> int:
    return int(item.get("completed_at_ns", 0))


def build_snapshot(state_root: Path, board_path: Path, lines: int = 120) -> dict[str, list[dict[str, Any]]]:
    pane_board, win_board = load_board_meta(board_path)
    now_ns = time.time_ns()
    unread_candidates: list[dict[str, Any]] = []
    recent_candidates: list[dict[str, Any]] = []

    for sock, sdir in discover_live_servers(state_root):
        by_sess_pane, by_pane = list_live_panes(sock)
        for state_file in sdir.glob("pane-*.json"):
            data = acs.read_json(state_file)
            pane_id = str(data.get("pane_id", "")).strip()
            if not pane_id:
                continue
            session_name = str(data.get("session_name", "")).strip()
            live_info = by_sess_pane.get((session_name, pane_id)) or by_pane.get(pane_id)
            if live_info is None or is_actively_working(live_info):
                continue
            try:
                completed_ns = int(data.get("completed_at_ns", 0))
            except (TypeError, ValueError):
                completed_ns = 0
            source = str(data.get("source", "")).strip()
            w_meta = win_board.get(live_info["window_id"], {})
            p_meta = pane_board.get(pane_id, {})
            summary = p_meta.get("summary") or tmux_panes.clean_title(
                live_info.get("title", ""), live_info.get("path", "")
            )
            entry = {
                "key": f"{sock}:{pane_id}",
                "tmux_socket": sock,
                "session_name": live_info["session_name"],
                "window_id": live_info["window_id"],
                "window_index": live_info["window_index"],
                "window_name": live_info["window_name"],
                "pane_id": pane_id,
                "source": source,
                "source_label": SOURCE_LABELS.get(source, source.title() or "Agent"),
                "unread": data.get("unread") is True,
                "completed_at_ns": completed_ns,
                "age": relative_age(completed_ns, now_ns),
                "summary": summary,
                "repo": w_meta.get("repo", ""),
                "branch": w_meta.get("branch", ""),
            }
            if entry["unread"]:
                unread_candidates.append(entry)
            else:
                recent_candidates.append(entry)

    unread_candidates.sort(key=_record_priority, reverse=True)
    recent_candidates.sort(key=_record_priority, reverse=True)
    kept_unread = unread_candidates[:12]
    kept_recent = recent_candidates[:5]

    for item in [*kept_unread, *kept_recent]:
        captured = run_tmux(
            item["tmux_socket"],
            "capture-pane", "-e", "-p", "-J", "-S", f"-{max(20, lines)}", "-t", item["pane_id"],
            timeout=1.5,
        )
        trimmed = trim_tui_footer(captured)
        item["compact_html"] = ansi_to_html(trimmed[-10:])
        item["full_html"] = ansi_to_html(trimmed[-100:])
        item["line_count"] = len(trimmed)

    return {"unread": kept_unread, "recent": kept_recent}


def refresh_badges(tmux_socket: str) -> None:
    server_pid = run_tmux(tmux_socket, "display-message", "-p", "#{pid}", timeout=0.5).strip()
    env = os.environ.copy()
    if tmux_socket and server_pid.isdigit():
        env["TMUX"] = f"{tmux_socket},{server_pid},0"
    if BADGES_REFRESH.is_file():
        try:
            subprocess.run(
                [str(BADGES_REFRESH), "--force"],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    run_tmux(tmux_socket, "refresh-client", "-S", timeout=0.5)


def ack_pane(state_root: Path, tmux_socket: str, pane_id: str) -> bool:
    changed = acs.acknowledge(state_root, pane_id, tmux_socket)
    refresh_badges(tmux_socket)
    return bool(changed)


def ack_all_panes(state_root: Path) -> int:
    count = 0
    touched_sockets: set[str] = set()
    for sock, sdir in discover_live_servers(state_root):
        for state_file in sdir.glob("pane-*.json"):
            data = acs.read_json(state_file)
            pane_id = str(data.get("pane_id", "")).strip()
            if data.get("unread") is True and pane_id:
                if acs.acknowledge(state_root, pane_id, sock):
                    count += 1
                    touched_sockets.add(sock)
    for sock in touched_sockets:
        refresh_badges(sock)
    return count


def open_pane(state_root: Path, tmux_socket: str, session_name: str, window_id: str, pane_id: str) -> None:
    acs.acknowledge(state_root, pane_id, tmux_socket)

    clients_raw = run_tmux(
        tmux_socket,
        "list-clients",
        "-F",
        "\t".join([
            "#{client_name}", "#{client_tty}", "#{client_termname}",
            "#{client_termtype}", "#{session_name}", "#{session_group}", "#{window_id}",
        ]),
        timeout=1.0,
    )
    parsed_clients: list[dict[str, str]] = []
    target_group = ""
    for line in clients_raw.splitlines():
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        c_name, c_tty, c_term, c_termtype, c_sess, c_group, c_wid = parts
        if c_sess == session_name and c_group:
            target_group = c_group
        parsed_clients.append({
            "client": c_name, "tty": c_tty, "session": c_sess,
            "group": c_group, "window_id": c_wid,
            "ghostty": "ghostty" in f"{c_term} {c_termtype}".lower(),
        })
    if not target_group:
        target_group = run_tmux(tmux_socket, "display-message", "-p", "-t", f"{session_name}:", "#{session_group}", timeout=0.5).strip()

    visible_client: dict[str, str] | None = None
    session_group_clients: list[dict[str, str]] = []
    for info in parsed_clients:
        if not info["ghostty"]:
            continue
        same = info["session"] == session_name or (target_group and info["group"] == target_group)
        if not same:
            continue
        if info["window_id"] == window_id:
            visible_client = info
            break
        session_group_clients.append(info)

    if visible_client is not None:
        run_tmux(tmux_socket, "select-pane", "-t", pane_id, timeout=1.0)
        focus_cmd = [
            str(FOCUS_SCRIPT),
            "--session", visible_client["session"],
            "--client", visible_client["client"],
            "--tty", visible_client["tty"],
            "--window-id", window_id,
            "--pane-id", pane_id,
        ]
    else:
        fallback_client = next(
            (c for c in session_group_clients if c["session"] == session_name),
            session_group_clients[0] if session_group_clients else None,
        )
        target_session = fallback_client["session"] if fallback_client else session_name
        run_tmux(tmux_socket, "select-window", "-t", f"{target_session}:{window_id}", timeout=1.0)
        run_tmux(tmux_socket, "select-pane", "-t", pane_id, timeout=1.0)
        focus_cmd = [
            str(FOCUS_SCRIPT),
            "--session", target_session,
            "--window-id", window_id,
            "--pane-id", pane_id,
        ]
        if fallback_client:
            focus_cmd.extend(["--client", fallback_client["client"], "--tty", fallback_client["tty"]])

    focused = False
    if FOCUS_SCRIPT.is_file():
        try:
            res = subprocess.run(focus_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            focused = res.returncode == 0
        except (OSError, subprocess.SubprocessError):
            focused = False
    if not focused:
        try:
            subprocess.run(["open", "-a", "Ghostty"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        except (OSError, subprocess.SubprocessError):
            pass

    refresh_badges(tmux_socket)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_snap = sub.add_parser("snapshot")
    p_snap.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_ROOT)
    p_snap.add_argument("--board-path", type=Path, default=DEFAULT_BOARD_PATH)
    p_snap.add_argument("--lines", type=int, default=120)

    p_ack = sub.add_parser("ack")
    p_ack.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_ROOT)
    p_ack.add_argument("--tmux-socket", required=True)
    p_ack.add_argument("--pane-id", required=True)

    p_ack_all = sub.add_parser("ack-all")
    p_ack_all.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_ROOT)

    p_open = sub.add_parser("open")
    p_open.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_ROOT)
    p_open.add_argument("--tmux-socket", required=True)
    p_open.add_argument("--session", required=True)
    p_open.add_argument("--window-id", required=True)
    p_open.add_argument("--pane-id", required=True)

    args = parser.parse_args()
    if args.cmd == "snapshot":
        payload = build_snapshot(args.state_dir.expanduser(), args.board_path.expanduser(), args.lines)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.cmd == "ack":
        changed = ack_pane(args.state_dir.expanduser(), args.tmux_socket, args.pane_id)
        print(json.dumps({"ok": True, "changed": changed}))
        return 0
    if args.cmd == "ack-all":
        count = ack_all_panes(args.state_dir.expanduser())
        print(json.dumps({"ok": True, "count": count}))
        return 0
    if args.cmd == "open":
        open_pane(args.state_dir.expanduser(), args.tmux_socket, args.session, args.window_id, args.pane_id)
        print(json.dumps({"ok": True}))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
