#!/usr/bin/env bash

set -euo pipefail

STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/codex-tmux-status"
RUN_COLOR="#0969DA"
RUN_MUTED_COLOR="#8C959F"
DONE_COLOR="#1A7F37"
SPINNER_RE='^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] '

state_path_for_pane() {
  printf '%s/pane-%s.json' "${STATE_DIR}" "${1#%}"
}

is_codex_command() {
  case "${1:-}" in
    codex|codex-*) return 0 ;;
    *) return 1 ;;
  esac
}

is_running_title() {
  printf '%s\n' "${1:-}" | LC_ALL=en_US.UTF-8 grep -Eq "${SPINNER_RE}"
}

gc_stale_state_files() {
  local path pane_num pane_id

  [[ -d "${STATE_DIR}" ]] || return 0

  for path in "${STATE_DIR}"/pane-*.json; do
    [[ -e "${path}" ]] || break
    pane_num=${path##*/pane-}
    pane_num=${pane_num%.json}
    pane_id="%${pane_num}"
    tmux display-message -p -t "${pane_id}" "#{pane_id}" >/dev/null 2>&1 || rm -f "${path}"
  done
}

render_running() {
  local phase dot_color

  phase=$(( ($(date +%s) / 2) % 2 ))
  dot_color="${RUN_COLOR}"

  if [[ ${phase} -eq 1 ]]; then
    dot_color="${RUN_MUTED_COLOR}"
  fi

  printf ' #[push-default]#[fg=%s,bold]●#[pop-default]' "${dot_color}"
}

render_done() {
  printf ' #[push-default]#[fg=%s,bold]󰄬#[pop-default]' "${DONE_COLOR}"
}

main() {
  local window_id window_active compact
  local pane_id pane_cmd pane_title running codex_panes path completed

  window_id=${1:-}
  window_active=${2:-0}
  compact=${3:-}

  [[ -n "${window_id}" ]] || exit 0

  gc_stale_state_files

  running=0
  codex_panes=""

  while IFS=$'\t' read -r pane_id pane_cmd pane_title; do
    is_codex_command "${pane_cmd}" || continue
    codex_panes="${codex_panes}${pane_id}"$'\n'
    if [[ ${running} -eq 0 ]] && is_running_title "${pane_title}"; then
      running=1
    fi
  done < <(tmux list-panes -t "${window_id}" -F "#{pane_id}"$'\t'"#{pane_current_command}"$'\t'"#{pane_title}" 2>/dev/null || true)

  if [[ ${running} -eq 1 ]]; then
    render_running "${compact}"
    exit 0
  fi

  [[ -n "${codex_panes}" ]] || exit 0

  completed=0
  while IFS= read -r pane_id; do
    [[ -n "${pane_id}" ]] || continue
    path=$(state_path_for_pane "${pane_id}")
    if [[ -f "${path}" ]]; then
      completed=1
      break
    fi
  done <<EOF
${codex_panes}
EOF

  [[ ${completed} -eq 1 ]] || exit 0

  if [[ "${window_active}" == "1" ]]; then
    while IFS= read -r pane_id; do
      [[ -n "${pane_id}" ]] || continue
      rm -f "$(state_path_for_pane "${pane_id}")"
    done <<EOF
${codex_panes}
EOF
    exit 0
  fi

  render_done
}

main "$@"
