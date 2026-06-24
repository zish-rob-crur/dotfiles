#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "claude-tmux-status"
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def collapse(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def tmux_context() -> dict[str, str]:
    pane = os.environ.get("TMUX_PANE", "")
    if not pane:
        return {}
    try:
        output = subprocess.check_output(
            [
                "tmux",
                "display-message",
                "-p",
                "-t",
                pane,
                "#{session_name}\t#{window_id}\t#{window_index}\t#{window_name}\t#{pane_id}",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return {}
    parts = output.split("\t", 4)
    if len(parts) != 5:
        return {}
    session_name, window_id, window_index, window_name, pane_id = parts
    if not session_name or not window_id.startswith("@") or not pane_id.startswith("%"):
        return {}
    return {
        "session_name": session_name,
        "window_id": window_id,
        "window_index": window_index,
        "window_name": window_name,
        "pane_id": pane_id,
    }


def state_path(pane_id: str) -> Path:
    return STATE_DIR / f"pane-{pane_id.lstrip('%')}.json"


def main() -> int:
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    session_id = collapse(hook_input.get("session_id", ""))
    if not UUID_RE.match(session_id):
        return 0

    context = tmux_context()
    if not context:
        return 0

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "tool": "claude",
        "pane_id": context["pane_id"],
        "window_id": context["window_id"],
        "window_index": context["window_index"],
        "window_name": context["window_name"],
        "session_name": context["session_name"],
        "session_id": session_id,
        "transcript_path": collapse(hook_input.get("transcript_path", "")),
        "cwd": collapse(hook_input.get("cwd", "")),
        "source": collapse(hook_input.get("source", "")),
        "hook_event_name": collapse(hook_input.get("hook_event_name", "")),
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    state_path(context["pane_id"]).write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
