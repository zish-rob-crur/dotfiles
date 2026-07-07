#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

usage() {
  cat >&2 <<EOF
Usage: ${SCRIPT_NAME} [--print-commands] [SPYMUX_ARGS...]

Preview Codex and Claude panes with spymux.
EOF
}

is_version_name() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

add_command() {
  local command="$1"

  [[ -n "${command}" ]] || return 0
  case ",${ASSISTANT_COMMANDS}," in
    *,"${command}",*) ;;
    *) ASSISTANT_COMMANDS="${ASSISTANT_COMMANDS:+${ASSISTANT_COMMANDS},}${command}" ;;
  esac
}

collect_commands() {
  local pane_lines command title path

  pane_lines="$(tmux list-panes -a -F "#{pane_current_command}"$'\t'"#{pane_title}"$'\t'"#{pane_current_path}" 2>/dev/null || true)"

  while IFS=$'\t' read -r command title path; do
    case "${command}" in
      codex|codex-*|claude|claude-*)
        add_command "${command}"
        continue
        ;;
    esac

    if [[ "${title}" == "✳ "* || "${path}" == *"/claude-envs/"* ]]; then
      add_command "${command}"
      continue
    fi

    if is_version_name "${command}"; then
      case "${title}" in
        "⠋ "*|"⠙ "*|"⠹ "*|"⠸ "*|"⠼ "*|"⠴ "*|"⠦ "*|"⠧ "*|"⠇ "*|"⠏ "*|"⠐ "*)
          add_command "${command}"
          ;;
      esac
    fi
  done <<< "${pane_lines}"
}

print_commands=0
while (($# > 0)); do
  case "$1" in
    --print-commands)
      print_commands=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

ASSISTANT_COMMANDS=""
collect_commands

if [[ "${print_commands}" == "1" ]]; then
  printf '%s\n' "${ASSISTANT_COMMANDS}"
  exit 0
fi

if [[ -z "${ASSISTANT_COMMANDS}" ]]; then
  tmux display-message "No Codex/Claude panes found" 2>/dev/null || true
  exit 0
fi

if ! command -v spymux >/dev/null 2>&1; then
  tmux display-message "spymux is not installed" 2>/dev/null || true
  exit 1
fi

REAL_TMUX="$(command -v tmux)"
CURRENT_SESSION="$(tmux display-message -p "#{session_name}" 2>/dev/null || true)"

exec env \
  ASSISTANT_SPYMUX_REAL_TMUX="${REAL_TMUX}" \
  ASSISTANT_SPYMUX_SESSION="${CURRENT_SESSION}" \
  PATH="${SCRIPT_DIR}/spymux-shim:${PATH}" \
  spymux -c "${ASSISTANT_COMMANDS}" "$@"
