#!/usr/bin/env python3

import json
import os
import re
import shutil
import subprocess
import sys

GHOSTTY_BUNDLE_ID = "com.mitchellh.ghostty"
OSC9_LIMIT = 180
TITLE_LIMIT = 72
SUBTITLE_LIMIT = 110
MESSAGE_LIMIT = 160


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalized_messages(messages: object) -> list[str]:
    if not isinstance(messages, list):
        return []
    cleaned: list[str] = []
    for item in messages:
        if not isinstance(item, str):
            continue
        text = collapse(item)
        if text and (not cleaned or cleaned[-1] != text):
            cleaned.append(text)
    return cleaned


def task_summary(messages: object) -> str:
    cleaned = normalized_messages(messages)
    if not cleaned:
        return ""
    last = cleaned[-1]
    if len(last) < 12 and len(cleaned) >= 2:
        return f"{cleaned[-2]} / {last}"
    return last


def shell_output(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def tmux_context() -> dict[str, str]:
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return {}
    output = shell_output(
        [
            "tmux",
            "display-message",
            "-p",
            "-t",
            pane,
            "#{client_tty}|#{client_termname}|#{client_termtype}|#{session_name}|#{window_index}|#{window_name}|#{pane_id}|#{pane_index}",
        ]
    )
    if not output:
        return {}
    parts = output.split("|", 7)
    if len(parts) != 8:
        return {}
    return {
        "client_tty": parts[0],
        "client_termname": parts[1],
        "client_termtype": parts[2],
        "session_name": parts[3],
        "window_index": parts[4],
        "window_name": parts[5],
        "pane_id": parts[6],
        "pane_index": parts[7],
    }


def tmux_label(context: dict[str, str]) -> str:
    if not context:
        return ""
    parts: list[str] = []
    session_name = context.get("session_name", "")
    window_index = context.get("window_index", "")
    window_name = context.get("window_name", "")
    pane_id = context.get("pane_id", "")

    if session_name:
        parts.append(session_name)
    if window_index or window_name:
        parts.append(f"{window_index}:{window_name}".strip(":"))
    if pane_id:
        parts.append(pane_id)
    return " · ".join(parts)


def direct_terminal_info() -> tuple[str, str, str]:
    tty = ""
    try:
        tty = os.ttyname(sys.stdout.fileno())
    except OSError:
        if os.path.exists("/dev/tty"):
            tty = "/dev/tty"
    return (
        tty,
        os.environ.get("TERM", ""),
        os.environ.get("TERM_PROGRAM", ""),
    )


def is_ghostty(term_name: str, term_type: str) -> bool:
    haystack = f"{term_name} {term_type}".lower()
    return "ghostty" in haystack


def send_osc9(tty_path: str, message: str) -> bool:
    if not tty_path or not message:
        return False
    sequence = f"\033]9;{message}\a".encode("utf-8", errors="ignore")
    try:
        fd = os.open(tty_path, os.O_WRONLY)
        try:
            os.write(fd, sequence)
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def send_terminal_notifier(title: str, subtitle: str, message: str, group: str) -> bool:
    notifier = shutil.which("terminal-notifier")
    if not notifier:
        return False
    command = [
        notifier,
        "-title",
        title,
        "-message",
        message or subtitle or title,
        "-group",
        group,
        "-activate",
        GHOSTTY_BUNDLE_ID,
        "-sender",
        GHOSTTY_BUNDLE_ID,
    ]
    if subtitle:
        command.extend(["-subtitle", subtitle])
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def main() -> int:
    if len(sys.argv) < 2:
        return 1

    try:
        notification = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        return 1

    if notification.get("type") != "agent-turn-complete":
        return 0

    cwd = collapse(str(notification.get("cwd", "")))
    project = os.path.basename(cwd.rstrip("/")) or "Codex"
    task = truncate(task_summary(notification.get("input-messages")), SUBTITLE_LIMIT)
    assistant = truncate(collapse(str(notification.get("last-assistant-message", ""))), MESSAGE_LIMIT)
    title = truncate(f"Codex 完成 · {project}", TITLE_LIMIT)

    context = tmux_context()
    tmux_location = tmux_label(context)
    subtitle = task or assistant or "任务完成"

    message_parts: list[str] = []
    if tmux_location:
        message_parts.append(f"tmux {tmux_location}")
    if assistant and assistant != subtitle:
        message_parts.append(assistant)
    elif cwd:
        message_parts.append(cwd)
    message = truncate(" | ".join(message_parts) or "任务完成", MESSAGE_LIMIT)
    group = "codex-" + truncate(collapse(str(notification.get("thread-id", ""))), 80)

    osc_parts = [title]
    if tmux_location:
        osc_parts.append(f"tmux {tmux_location}")
    if subtitle:
        osc_parts.append(subtitle)
    osc_message = truncate(" · ".join(osc_parts), OSC9_LIMIT)

    client_tty = context.get("client_tty", "")
    client_termname = context.get("client_termname", "")
    client_termtype = context.get("client_termtype", "")
    if is_ghostty(client_termname, client_termtype):
        if send_osc9(client_tty, osc_message):
            return 0

    tty, term_name, term_type = direct_terminal_info()
    if is_ghostty(term_name, term_type):
        if send_osc9(tty, osc_message):
            return 0

    send_terminal_notifier(title, subtitle, message, group)
    return 0


if __name__ == "__main__":
    sys.exit(main())
