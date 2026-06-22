#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/codex-tmux-status"

payload="$(cat)"
tmux_meta=""
hook_cwd=""

hook_cwd=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("cwd", ""))' <<<"${payload}" 2>/dev/null || true)

is_claude_pane() {
  local command title path
  command=${1:-}
  title=${2:-}
  path=${3:-}

  case "${command}" in
    claude|claude-*) return 0 ;;
  esac

  if [[ "${command}" =~ ^[0-9]+[.][0-9]+[.][0-9]+$ && "${title}" == "✳ "* ]]; then
    return 0
  fi

  [[ "${path}" == *"/claude-envs/"* ]]
}

if [[ -n "${TMUX_PANE:-}" ]]; then
  tmux_meta=$(tmux display-message -p -t "${TMUX_PANE}" \
    "#{session_name}"$'\t'"#{window_id}"$'\t'"#{window_index}"$'\t'"#{window_name}"$'\t'"#{pane_id}" 2>/dev/null || true)
fi

if [[ -z "${tmux_meta}" && -n "${hook_cwd}" ]]; then
  while IFS=$'\t' read -r session_name window_id window_index window_name pane_id pane_active pane_command pane_title pane_path; do
    [[ -n "${pane_id}" ]] || continue
    [[ "${pane_path}" == "${hook_cwd}" ]] || continue
    is_claude_pane "${pane_command}" "${pane_title}" "${pane_path}" || continue
    tmux_meta="${session_name}"$'\t'"${window_id}"$'\t'"${window_index}"$'\t'"${window_name}"$'\t'"${pane_id}"
    [[ "${pane_active}" == "1" ]] && break
  done < <(tmux list-panes -a -F "#{session_name}"$'\t'"#{window_id}"$'\t'"#{window_index}"$'\t'"#{window_name}"$'\t'"#{pane_id}"$'\t'"#{pane_active}"$'\t'"#{pane_current_command}"$'\t'"#{pane_title}"$'\t'"#{pane_current_path}" 2>/dev/null || true)
fi

mkdir -p "${STATE_DIR}"

STATE_DIR="${STATE_DIR}" TMUX_META="${tmux_meta}" python3 - "${payload}" <<'PY'
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def collapse(text: object) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def latest_assistant_text(transcript_path: object) -> str:
    if not isinstance(transcript_path, str) or not transcript_path:
        return ""

    path = Path(transcript_path)
    if not path.is_file():
        return ""

    latest = ""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                message = entry.get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue

                content = message.get("content")
                if not isinstance(content, list):
                    continue

                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = collapse(block.get("text", ""))
                        if text:
                            latest = text
    except OSError:
        return ""

    return latest


try:
    hook_input = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] else {}
except json.JSONDecodeError:
    hook_input = {}

tmux_meta = os.environ.get("TMUX_META", "")
if tmux_meta:
    parts = tmux_meta.split("\t", 4)
    if len(parts) == 5:
        session_name, window_id, window_index, window_name, pane_id = parts
        if session_name and window_id.startswith("@") and pane_id.startswith("%"):
            state_dir = os.environ["STATE_DIR"]
            state_path = os.path.join(state_dir, f"pane-{pane_id.lstrip('%')}.json")
            payload = {
                "source": "claude",
                "pane_id": pane_id,
                "window_id": window_id,
                "window_index": window_index,
                "window_name": window_name,
                "session_name": session_name,
                "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "cwd": collapse(hook_input.get("cwd", "")),
                "summary": collapse(hook_input.get("session_id", "")),
                "assistant": latest_assistant_text(hook_input.get("transcript_path")),
                "thread_id": collapse(hook_input.get("session_id", "")),
                "transcript_path": collapse(hook_input.get("transcript_path", "")),
                "unread": True,
            }
            with open(state_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)

print("{}")
PY

if [[ -x "${SCRIPT_DIR}/codex-window-badges-refresh.sh" ]]; then
  "${SCRIPT_DIR}/codex-window-badges-refresh.sh" --force >/dev/null 2>&1 || true
fi

tmux refresh-client -S >/dev/null 2>&1 || true
