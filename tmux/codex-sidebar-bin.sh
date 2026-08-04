#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/codex-tmux-status"
DESKTOP_NOTIFY_SCRIPT="${SCRIPT_DIR}/codex-notify-ghostty.py"
STATE_HELPER="${SCRIPT_DIR}/assistant_completion_state.py"

handle_notify() {
  local payload tmux_meta classification classified_root
  local tmux_socket tmux_tail inherited_server_pid captured_server_pid

  classified_root=false
  if [[ "${1:-}" == "--classified-root" ]]; then
    classified_root=true
    shift
  fi

  if [[ $# -ge 1 ]]; then
    payload=$1
  else
    payload=$(cat)
  fi

  # Direct/manual invocation is still fail-closed. The outer router supplies
  # --classified-root so Sky, this sidebar, and the desktop notifier all reuse
  # the same SQLite snapshot instead of racing through separate queries.
  if [[ "${classified_root}" != true ]]; then
    classification=$(python3 "${DESKTOP_NOTIFY_SCRIPT}" --classify "${payload}" 2>/dev/null || true)
    [[ "${classification}" == "root" ]] || return 0
  fi

  tmux_meta=""
  tmux_socket=""
  inherited_server_pid=""
  if [[ -n "${TMUX_PANE:-}" && -n "${TMUX:-}" ]]; then
    tmux_socket=${TMUX%%,*}
    tmux_tail=${TMUX#*,}
    inherited_server_pid=${tmux_tail%%,*}
    if [[ "${inherited_server_pid}" =~ ^[1-9][0-9]*$ ]]; then
      tmux_meta=$(tmux -S "${tmux_socket}" display-message -p -t "${TMUX_PANE}" \
        "#{session_name}"$'\t'"#{window_id}"$'\t'"#{window_index}"$'\t'"#{window_name}"$'\t'"#{pane_id}"$'\t'"#{socket_path}"$'\t'"#{pid}"$'\t'"#{start_time}" 2>/dev/null || true)
      IFS=$'\t' read -r _ _ _ _ _ _ captured_server_pid _ <<< "${tmux_meta}"
      [[ "${captured_server_pid}" == "${inherited_server_pid}" ]] || tmux_meta=""
    fi
  fi

  # State persistence is useful for tmux badges, but it must never suppress a
  # root desktop completion notification if disk or JSON handling fails.
  python3 "${STATE_HELPER}" \
    --state-dir "${STATE_DIR}" \
    --tmux-meta "${tmux_meta}" \
    --write-codex \
    "${payload}" >/dev/null 2>&1 || true

  if [[ -n "${TMUX:-}" ]]; then
    tmux -S "${TMUX%%,*}" refresh-client -S >/dev/null 2>&1 || true
  fi

  if [[ -f "${DESKTOP_NOTIFY_SCRIPT}" ]]; then
    python3 "${DESKTOP_NOTIFY_SCRIPT}" --classified-root "${payload}" >/dev/null 2>&1 || true
  fi
}

case "${1:-}" in
  notify)
    shift
    handle_notify "$@"
    ;;
  *)
    echo "usage: $(basename "$0") notify [--classified-root] '<json>'" >&2
    exit 1
    ;;
esac
