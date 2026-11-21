#!/usr/bin/env python3
"""Snapshot, acknowledge, and focus completed tmux agent panes for Hammerspoon HUD."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
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
WORKED_FOR_RE = re.compile(
    r"^[✻✳✶✽✢·✦✧•*\s]*(?:Worked|Baked|Churned|Cooked|Cogitated|Brewed|Thought)\s+for\s+"
    r"(\d+[dhms](?:\s+\d+[dhms])*)(?:\s*[•·]\s*\d{1,2}:\d{2}(?::\d{2})?)?\s*$",
    re.IGNORECASE,
)
WINDOW_ICON_PREFIX_RE = re.compile(r"^[^\w\u4e00-\u9fff\u3400-\u4dbf~./@-]+\s+")
STRUCTURAL_START_RE = re.compile(
    r"^(?:[•◦▪▸*\-+]|\d+[.)]|#{1,6})\s+|^(?:[›❯>]|```|~~~|\+\s+\d+\s+lines\b|Ran\s+)"
)
BOX_DRAWING_CHARS_RE = re.compile(r"[┌┐└┘├┤┬┴┼╭╮╰╯│┃═]")
LINK_TOKEN_RE = re.compile(
    r"(?P<url>https?://[^\s<>\"'`)\]]+)"
    r"|(?<![A-Za-z0-9_:/<>=])(?P<path>(?:~/|\.\.?/|/|[A-Za-z0-9_.-]+/)[A-Za-z0-9_./@+-]*[A-Za-z0-9_/-](?::\d+(?::\d+)?)?)"
)
BROWSER_DOC_EXTS = {".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

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


def clean_window_name(name: str) -> str:
    return WINDOW_ICON_PREFIX_RE.sub("", name.strip()).strip() or name.strip()


def extract_turn_duration(lines: list[str]) -> tuple[list[str], str]:
    """Pop trailing 'Worked for 1m 46s • 11:01' line if prose remains above it."""
    if not lines:
        return lines, ""
    last_plain = strip_ansi(lines[-1]).strip()
    match = WORKED_FOR_RE.match(last_plain)
    if not match:
        return lines, ""
    duration = re.sub(r"\s+", " ", match.group(1)).strip()
    trimmed = lines[:-1]
    while trimmed and not strip_ansi(trimmed[-1]).strip():
        trimmed.pop()
    return (trimmed if trimmed else lines), duration


def _visual_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _is_cjk_or_fullwidth(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x2E80 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0xF900 <= cp <= 0xFAFF
        or 0xFF00 <= cp <= 0xFFEF
    )


def _lstrip_ansi_spaces(line: str, max_spaces: int | None = None) -> str:
    """Drop up to max_spaces leading ASCII spaces while preserving leading ANSI codes."""
    out: list[str] = []
    pos = 0
    removed = 0
    done_stripping = False
    while pos < len(line):
        if not done_stripping:
            seq = ANSI_ANY_RE.match(line, pos)
            if seq:
                out.append(seq.group(0))
                pos = seq.end()
                continue
            if line[pos] == " " and (max_spaces is None or removed < max_spaces):
                removed += 1
                pos += 1
                continue
            done_stripping = True
        out.append(line[pos:])
        break
    return "".join(out)


def _is_structural_line(plain: str) -> bool:
    stripped = plain.strip()
    if not stripped:
        return True
    if STRUCTURAL_START_RE.match(stripped):
        return True
    if BOX_DRAWING_CHARS_RE.search(plain):
        return True
    if stripped.startswith(("|", "+ ", "- ", "@@ ", "$ ", "% ")):
        return True
    if re.fullmatch(r"[\s\-─━═_=*]{3,}", stripped):
        return True
    return False


def _prev_allows_reflow(plain: str) -> bool:
    stripped = plain.strip()
    if not stripped:
        return False
    if re.match(r"^(?:#{1,6}\s+|[›❯>]|```|~~~|\+\s+\d+\s+lines\b|Ran\s+)", stripped):
        return False
    if BOX_DRAWING_CHARS_RE.search(plain) or stripped.startswith(("|", "+ ", "- ", "@@ ")):
        return False
    if re.fullmatch(r"[\s\-─━═_=*]{3,}", stripped):
        return False
    return True


def reflow_prose(lines: list[str]) -> list[str]:
    """Join TUI hard-wrapped prose lines and dedent common 2-space left margin."""
    if not lines:
        return []
    merged: list[str] = []
    merged_plain: list[str] = []
    last_raw_width = 0
    in_fence = False

    for raw in lines:
        plain = strip_ansi(raw)
        stripped = plain.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            merged.append(raw)
            merged_plain.append(plain)
            last_raw_width = 0
            continue
        if in_fence or not stripped:
            merged.append(raw)
            merged_plain.append(plain)
            last_raw_width = 0
            continue

        curr_indent = len(plain) - len(plain.lstrip(" "))
        raw_width = _visual_width(plain)
        wraps_mid_token = bool(merged_plain) and merged_plain[-1].rstrip().endswith(("/", "-", "（", "("))
        if (
            merged
            and (last_raw_width >= 38 or wraps_mid_token)
            and curr_indent <= 4
            and _prev_allows_reflow(merged_plain[-1])
            and not _is_structural_line(plain)
        ):
            prev_r = merged_plain[-1].rstrip()
            curr_l = plain.lstrip(" ")
            p_last = prev_r[-1]
            c_first = curr_l[0]
            if p_last in "/-—–(“‘「『【（":
                sep = ""
            elif _is_cjk_or_fullwidth(p_last) or _is_cjk_or_fullwidth(c_first):
                sep = ""
            else:
                sep = " "
            merged[-1] = merged[-1].rstrip(" ") + sep + _lstrip_ansi_spaces(raw, None)
            merged_plain[-1] = prev_r + sep + curr_l
            last_raw_width = raw_width
        else:
            merged.append(raw)
            merged_plain.append(plain)
            last_raw_width = raw_width

    nonempty_indents = [
        len(p) - len(p.lstrip(" "))
        for p in merged_plain
        if p.strip()
    ]
    if not nonempty_indents:
        return merged

    min_indent = min(nonempty_indents)
    if min_indent >= 1:
        drop = min(min_indent, 4)
        return [_lstrip_ansi_spaces(ln, drop) for ln in merged]

    # Codex starts turn line 1 with '• ' at col 0 and indents subsequent paragraphs by 2 spaces.
    out: list[str] = []
    in_fence = False
    for idx, (ln, plain) in enumerate(zip(merged, merged_plain)):
        st = plain.strip()
        if st.startswith(("```", "~~~")):
            in_fence = not in_fence
            out.append(ln)
            continue
        indent = len(plain) - len(plain.lstrip(" "))
        prev_blank = idx > 0 and not merged_plain[idx - 1].strip()
        if not in_fence and indent == 2 and (prev_blank or st.startswith("• ")):
            out.append(_lstrip_ansi_spaces(ln, 2))
        else:
            out.append(ln)
    return out


def _is_plausible_path(token: str) -> bool:
    core = re.sub(r":\d+(?::\d+)?$", "", token)
    if not core or core.startswith("//") or core == "/":
        return False
    if core.startswith(("~/", "./", "../")):
        return len(core) >= 3
    if core.startswith("/"):
        return core.count("/") >= 2
    if core.count("/") >= 2:
        return True
    if core.endswith("/"):
        stem = core[:-1]
        return len(stem) >= 3 and bool(re.fullmatch(r"[a-z0-9_.-]+", stem))
    return bool(re.search(r"/[^/\s]+\.[A-Za-z0-9]{1,8}$", core))


def _render_chunk_with_links(raw_chunk: str) -> str:
    parts: list[str] = []
    pos = 0
    for m in LINK_TOKEN_RE.finditer(raw_chunk):
        url = m.group("url")
        path = m.group("path")
        if path and not _is_plausible_path(path):
            continue
        start, end = m.span()
        if start > pos:
            parts.append(html.escape(raw_chunk[pos:start], quote=False))
        token_text = html.escape(raw_chunk[start:end], quote=False)
        if url:
            attr = html.escape(url, quote=True)
            parts.append(f'<a class="out-link" data-url="{attr}" title="Open URL">{token_text}</a>')
        else:
            attr = html.escape(path, quote=True)
            parts.append(f'<a class="out-link" data-path="{attr}" title="Open {attr}">{token_text}</a>')
        pos = end
    if pos < len(raw_chunk):
        parts.append(html.escape(raw_chunk[pos:], quote=False))
    return "".join(parts)


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
    """Convert ANSI SGR lines into safe HTML spans, coalescing identical adjacent styles."""
    style: dict[str, Any] = {
        "fg": None, "bg": None, "bold": False, "dim": False, "italic": False, "underline": False
    }
    out_lines: list[str] = []
    for line in lines:
        segments: list[list[str]] = []
        pos = 0
        for match in SGR_OR_SEQ_RE.finditer(line):
            if match.start() > pos:
                raw_chunk = line[pos:match.start()]
                css = _style_css(style)
                if segments and segments[-1][0] == css:
                    segments[-1][1] += raw_chunk
                else:
                    segments.append([css, raw_chunk])
            if match.group(1) is not None:
                _apply_sgr(match.group(1), style)
            pos = match.end()
        if pos < len(line):
            raw_chunk = line[pos:]
            css = _style_css(style)
            if segments and segments[-1][0] == css:
                segments[-1][1] += raw_chunk
            else:
                segments.append([css, raw_chunk])
        parts = [
            f'<span style="{css}">{_render_chunk_with_links(raw_chunk)}</span>'
            if css else _render_chunk_with_links(raw_chunk)
            for css, raw_chunk in segments
        ]
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
                "window_name": clean_window_name(live_info["window_name"]),
                "pane_id": pane_id,
                "cwd": live_info.get("path", ""),
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
        trimmed, duration = extract_turn_duration(trimmed)
        reflowed = reflow_prose(trimmed)
        item["duration"] = duration
        item["compact_html"] = ansi_to_html(reflowed[-18:])
        item["full_html"] = ansi_to_html(reflowed[-100:])
        item["plain_text"] = "\n".join(strip_ansi(ln) for ln in reflowed[-100:]).strip()
        item["line_count"] = len(reflowed)

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


def open_local_path(cwd: str, raw_path: str) -> bool:
    token = raw_path.strip()
    if not token:
        return False
    line_num = ""
    loc_match = re.match(r"^(.*?):(\d+)(?::\d+)?$", token)
    if loc_match:
        token, line_num = loc_match.group(1), loc_match.group(2)
    expanded = Path(token).expanduser()
    if not expanded.is_absolute():
        base = Path(cwd).expanduser() if cwd else Path.cwd()
        target = (base / expanded).resolve()
    else:
        target = expanded.resolve()
    if not target.exists():
        return False
    try:
        if target.is_dir() or target.suffix.lower() in BROWSER_DOC_EXTS:
            subprocess.run(["open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            return True
        neovide = shutil.which("neovide") or ("/opt/homebrew/bin/neovide" if Path("/opt/homebrew/bin/neovide").is_file() else "")
        if neovide:
            cmd = [neovide, str(target)]
            if line_num:
                cmd.append(f"+{line_num}")
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        subprocess.run(["open", "-R", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


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

    p_path = sub.add_parser("open-path")
    p_path.add_argument("--cwd", default="")
    p_path.add_argument("--path", required=True)

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
    if args.cmd == "open-path":
        opened = open_local_path(args.cwd, args.path)
        print(json.dumps({"ok": opened}))
        return 0 if opened else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
