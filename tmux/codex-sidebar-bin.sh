#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/codex-tmux-status"
DESKTOP_NOTIFY_SCRIPT="${SCRIPT_DIR}/codex-notify-ghostty.py"

handle_notify() {
  local payload tmux_meta

  if [[ $# -ge 1 ]]; then
    payload=$1
  else
    payload=$(cat)
  fi

  tmux_meta=""
  if [[ -n "${TMUX_PANE:-}" ]]; then
    tmux_meta=$(tmux display-message -p -t "${TMUX_PANE}" \
      "#{session_name}"$'\t'"#{window_id}"$'\t'"#{window_index}"$'\t'"#{window_name}"$'\t'"#{pane_id}" 2>/dev/null || true)
  fi

  mkdir -p "${STATE_DIR}"

  STATE_DIR="${STATE_DIR}" TMUX_META="${tmux_meta}" python3 - "${payload}" <<'PY'
import json
import os
import re
import sys
from datetime import datetime, timezone


def collapse(text: object) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


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


notification = json.loads(sys.argv[1])
if notification.get("type") != "agent-turn-complete":
    raise SystemExit(0)

tmux_meta = os.environ.get("TMUX_META", "")
if tmux_meta:
    session_name, window_id, window_index, window_name, pane_id = tmux_meta.split("\t", 4)
    state_dir = os.environ["STATE_DIR"]
    state_path = os.path.join(state_dir, f"pane-{pane_id.lstrip('%')}.json")
    payload = {
        "pane_id": pane_id,
        "window_id": window_id,
        "window_index": window_index,
        "window_name": window_name,
        "session_name": session_name,
        "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "cwd": collapse(notification.get("cwd", "")),
        "summary": collapse(task_summary(notification.get("input-messages"))),
        "assistant": collapse(notification.get("last-assistant-message", "")),
        "thread_id": collapse(notification.get("thread-id", "")),
        "unread": True,
    }
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
PY

  tmux refresh-client -S >/dev/null 2>&1 || true

  if [[ -f "${DESKTOP_NOTIFY_SCRIPT}" ]]; then
    python3 "${DESKTOP_NOTIFY_SCRIPT}" "${payload}" >/dev/null 2>&1 || true
  fi
}

case "${1:-}" in
  notify)
    shift
    handle_notify "$@"
    ;;
  *)
    echo "usage: $(basename "$0") notify '<json>'" >&2
    exit 1
    ;;
esac
