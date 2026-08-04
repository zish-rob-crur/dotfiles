#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/codex-tmux-status"
STATE_HELPER="${SCRIPT_DIR}/assistant_completion_state.py"

payload=$(cat)
event_ns=$(python3 -c 'import time; print(time.time_ns())' 2>/dev/null || true)
tmux_meta=""
hook_cwd=""
tmux_socket=""
inherited_server_pid=""

hook_cwd=$(python3 -c 'import json,sys; value=json.load(sys.stdin); print(value.get("cwd", "") if isinstance(value, dict) else "")' <<<"${payload}" 2>/dev/null || true)

if [[ -n "${TMUX:-}" ]]; then
  tmux_socket=${TMUX%%,*}
  tmux_tail=${TMUX#*,}
  inherited_server_pid=${tmux_tail%%,*}
  [[ "${inherited_server_pid}" =~ ^[1-9][0-9]*$ ]] || tmux_socket=""
fi

if [[ -n "${TMUX_PANE:-}" && -n "${tmux_socket}" ]]; then
  tmux_meta=$(tmux -S "${tmux_socket}" display-message -p -t "${TMUX_PANE}" \
    "#{session_name}"$'\t'"#{window_id}"$'\t'"#{window_index}"$'\t'"#{window_name}"$'\t'"#{pane_id}"$'\t'"#{socket_path}"$'\t'"#{pid}"$'\t'"#{start_time}" 2>/dev/null || true)
  IFS=$'\t' read -r _ _ _ _ _ _ captured_server_pid _ <<< "${tmux_meta}"
  [[ "${captured_server_pid}" == "${inherited_server_pid}" ]] || tmux_meta=""
fi

# Claude hooks do not always inherit TMUX_PANE. A cwd is not an identity: two
# Claude panes can legitimately share it, and session groups list the same
# physical pane more than once. Only a single unique physical match is safe.
if [[ -z "${tmux_meta}" && -z "${TMUX_PANE:-}" && -n "${hook_cwd}" && -n "${tmux_socket}" ]]; then
  matches=()
  seen_panes=" "
  while IFS=$'\t' read -r session_name window_id window_index window_name pane_id pane_command pane_title pane_path server_pid server_start_time server_socket; do
    [[ -n "${pane_id}" ]] || continue
    [[ "${seen_panes}" != *" ${pane_id} "* ]] || continue
    seen_panes+="${pane_id} "
    [[ "${pane_path}" == "${hook_cwd}" ]] || continue
    [[ "${server_pid}" == "${inherited_server_pid}" ]] || continue
    matches+=("${session_name}"$'\t'"${window_id}"$'\t'"${window_index}"$'\t'"${window_name}"$'\t'"${pane_id}"$'\t'"${server_socket}"$'\t'"${server_pid}"$'\t'"${server_start_time}")
  done < <(tmux -S "${tmux_socket}" list-panes -a -F "#{session_name}"$'\t'"#{window_id}"$'\t'"#{window_index}"$'\t'"#{window_name}"$'\t'"#{pane_id}"$'\t'"#{pane_current_command}"$'\t'"#{pane_title}"$'\t'"#{pane_current_path}"$'\t'"#{pid}"$'\t'"#{start_time}"$'\t'"#{socket_path}" 2>/dev/null || true)

  if [[ ${#matches[@]} -eq 1 ]]; then
    tmux_meta=${matches[0]}
  fi
fi

CODEX_NOTIFY_RECEIVED_NS="${event_ns}" python3 "${STATE_HELPER}" \
  --state-dir "${STATE_DIR}" \
  --tmux-meta "${tmux_meta}" \
  --write-claude \
  "${payload}" >/dev/null 2>&1 || true

if [[ -n "${tmux_meta}" && -x "${SCRIPT_DIR}/codex-window-badges-refresh.sh" ]]; then
  "${SCRIPT_DIR}/codex-window-badges-refresh.sh" --force >/dev/null 2>&1 || true
fi

if [[ -n "${tmux_meta}" ]]; then
  tmux -S "${tmux_socket}" refresh-client -S >/dev/null 2>&1 || true
fi
printf '{}\n'
