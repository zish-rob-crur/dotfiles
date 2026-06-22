#!/usr/bin/env bash

set -euo pipefail

STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/codex-tmux-status"
REFRESH_INTERVAL="${CODEX_TMUX_BADGE_REFRESH_INTERVAL:-2}"
DAEMON_INTERVAL="${CODEX_TMUX_BADGE_DAEMON_INTERVAL:-2}"
GC_INTERVAL="${CODEX_TMUX_BADGE_GC_INTERVAL:-60}"
RUN_COLOR="#0969DA"
RUN_MUTED_COLOR="#8C959F"
DONE_COLOR="#1A7F37"
FORCE_REFRESH=0
MODE="${1:-}"

state_path_for_pane() {
  printf '%s/pane-%s.json' "${STATE_DIR}" "${1#%}"
}

is_agent_pane() {
  local command title
  command=${1:-}
  title=${2:-}

  case "${1:-}" in
    codex|codex-*|claude|claude-*) return 0 ;;
  esac

  if [[ "${command}" =~ ^[0-9]+[.][0-9]+[.][0-9]+$ && "${title}" == "✳ "* ]]; then
    return 0
  fi

  return 1
}

is_running_title() {
  case "${1:-}" in
    "⠋ "*|"⠙ "*|"⠹ "*|"⠸ "*|"⠼ "*|"⠴ "*|"⠦ "*|"⠧ "*|"⠇ "*|"⠏ "*) return 0 ;;
    *) return 1 ;;
  esac
}

has_token() {
  local haystack needle
  haystack=$1
  needle=$2

  case "${haystack}" in
    *"|${needle}|"*) return 0 ;;
    *) return 1 ;;
  esac
}

append_unique_token() {
  local haystack needle
  haystack=$1
  needle=$2

  if has_token "${haystack}" "${needle}"; then
    printf '%s' "${haystack}"
  else
    printf '%s%s|' "${haystack}" "${needle}"
  fi
}

old_badge_for_window() {
  local old_badges window_id line id value
  old_badges=$1
  window_id=$2

  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    id=${line%%$'\t'*}
    value=${line#*$'\t'}
    if [[ "${id}" == "${window_id}" ]]; then
      printf '%s' "${value}"
      return 0
    fi
  done <<EOF
${old_badges}
EOF
}

should_refresh() {
  local stamp_path now last

  mkdir -p "${STATE_DIR}"

  if [[ "${FORCE_REFRESH}" == "1" ]]; then
    return 0
  fi

  stamp_path="${STATE_DIR}/.refresh-stamp"
  now=$(date +%s)
  last=0
  if [[ -f "${stamp_path}" ]]; then
    IFS= read -r last < "${stamp_path}" || last=0
  fi
  [[ ${last} =~ ^[0-9]+$ ]] || last=0
  if (( now - last < REFRESH_INTERVAL )); then
    return 1
  fi
  printf '%s\n' "${now}" > "${stamp_path}"
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

gc_stale_state_files() {
  local current_panes path pane_num pane_id stamp_path now last

  [[ -d "${STATE_DIR}" ]] || return 0

  stamp_path="${STATE_DIR}/.gc-stamp"
  now=$(date +%s)
  last=0
  if [[ -f "${stamp_path}" ]]; then
    IFS= read -r last < "${stamp_path}" || last=0
  fi
  [[ ${last} =~ ^[0-9]+$ ]] || last=0
  if (( now - last < GC_INTERVAL )); then
    return 0
  fi
  printf '%s\n' "${now}" > "${stamp_path}"

  current_panes="|"
  while IFS= read -r pane_id; do
    [[ -n "${pane_id}" ]] || continue
    current_panes="${current_panes}${pane_id}|"
  done < <(tmux list-panes -a -F "#{pane_id}" 2>/dev/null || true)

  for path in "${STATE_DIR}"/pane-*.json; do
    [[ -e "${path}" ]] || break
    pane_num=${path##*/pane-}
    pane_num=${pane_num%.json}
    pane_id="%${pane_num}"
    has_token "${current_panes}" "${pane_id}" || rm -f "${path}"
  done
}

main() {
  local windows active_windows old_badges running_windows done_windows
  local window_id window_active old_badge pane_id pane_cmd pane_title badge state_path

  should_refresh || return 0
  gc_stale_state_files

  windows=$'\n'
  active_windows="|"
  old_badges=""
  running_windows="|"
  done_windows="|"

  while IFS=$'\t' read -r window_id window_active old_badge; do
    [[ -n "${window_id}" ]] || continue
    windows="${windows}${window_id}"$'\n'
    old_badges="${old_badges}${window_id}"$'\t'"${old_badge}"$'\n'
    if [[ "${window_active}" == "1" ]]; then
      active_windows="${active_windows}${window_id}|"
    fi
  done < <(tmux list-windows -a -F "#{window_id}"$'\t'"#{window_active}"$'\t'"#{@codex-badge}" 2>/dev/null || true)

  while IFS=$'\t' read -r window_id pane_id pane_cmd pane_title; do
    [[ -n "${window_id}" && -n "${pane_id}" ]] || continue
    is_agent_pane "${pane_cmd}" "${pane_title}" || continue

    if is_running_title "${pane_title}"; then
      running_windows=$(append_unique_token "${running_windows}" "${window_id}")
      continue
    fi

    state_path=$(state_path_for_pane "${pane_id}")
    if [[ -f "${state_path}" ]]; then
      if has_token "${active_windows}" "${window_id}"; then
        rm -f "${state_path}"
      else
        done_windows=$(append_unique_token "${done_windows}" "${window_id}")
      fi
    fi
  done < <(tmux list-panes -a -F "#{window_id}"$'\t'"#{pane_id}"$'\t'"#{pane_current_command}"$'\t'"#{pane_title}" 2>/dev/null || true)

  while IFS= read -r window_id; do
    [[ -n "${window_id}" ]] || continue

    badge=""
    if has_token "${running_windows}" "${window_id}"; then
      badge=$(render_running)
    elif has_token "${done_windows}" "${window_id}"; then
      badge=$(render_done)
    fi

    old_badge=$(old_badge_for_window "${old_badges}" "${window_id}")
    if [[ "${old_badge}" != "${badge}" ]]; then
      tmux set-window-option -q -t "${window_id}" @codex-badge "${badge}" >/dev/null 2>&1 || true
    fi
  done <<EOF
${windows}
EOF
}

run_daemon() {
  local lock_dir pid digest_path old_digest script_digest

  mkdir -p "${STATE_DIR}"
  lock_dir="${STATE_DIR}/.refresh-daemon.lock"
  digest_path="${lock_dir}/script-sha256"
  script_digest=$(shasum -a 256 "$0" 2>/dev/null | awk '{print $1}' || true)

  if ! mkdir "${lock_dir}" 2>/dev/null; then
    pid=""
    if [[ -f "${lock_dir}/pid" ]]; then
      IFS= read -r pid < "${lock_dir}/pid" || pid=""
    fi
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      old_digest=""
      if [[ -f "${digest_path}" ]]; then
        IFS= read -r old_digest < "${digest_path}" || old_digest=""
      fi
      if [[ -n "${script_digest}" && "${old_digest}" != "${script_digest}" ]]; then
        kill "${pid}" >/dev/null 2>&1 || true
        sleep 0.2
      else
        exit 0
      fi
    fi
    rm -rf "${lock_dir}"
    mkdir "${lock_dir}" 2>/dev/null || exit 0
  fi

  printf '%s\n' "$$" > "${lock_dir}/pid"
  if [[ -n "${script_digest}" ]]; then
    printf '%s\n' "${script_digest}" > "${digest_path}"
  fi
  cleanup_daemon() {
    rm -rf "${lock_dir}"
  }
  trap cleanup_daemon EXIT
  trap 'cleanup_daemon; exit 0' INT TERM

  FORCE_REFRESH=1
  while tmux has-session >/dev/null 2>&1; do
    main || true
    sleep "${DAEMON_INTERVAL}"
  done
}

case "${MODE}" in
  --daemon)
    run_daemon
    ;;
  --force)
    FORCE_REFRESH=1
    main
    ;;
  *)
    main
    ;;
esac
