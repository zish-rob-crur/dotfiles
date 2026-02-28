#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
preview_script="${script_dir}/fzf-goto-preview.sh"

session="$(tmux display-message -p '#S' 2>/dev/null || true)"
if [[ -z "$session" ]]; then
  exit 0
fi

tmux_fzf_envs="${HOME}/.tmux/plugins/tmux-fzf/scripts/.envs"
if [[ -f "$tmux_fzf_envs" ]]; then
  # shellcheck source=/dev/null
  source "$tmux_fzf_envs"
fi

fzf_bin="${TMUX_FZF_BIN:-}"
if [[ -z "$fzf_bin" ]]; then
  if command -v fzf-tmux >/dev/null 2>&1; then
    fzf_bin="$(command -v fzf-tmux)"
  elif command -v fzf >/dev/null 2>&1; then
    fzf_bin="$(command -v fzf)"
  else
    tmux display-message "fzf not found"
    exit 0
  fi
fi

is_fzf_tmux=0
case "$fzf_bin" in
  *fzf-tmux | */.fzf-tmux) is_fzf_tmux=1 ;;
esac

fzf_cmd=("$fzf_bin")
if [[ -n "${TMUX_FZF_OPTIONS:-}" && $is_fzf_tmux -eq 1 ]]; then
  # shellcheck disable=SC2206
  fzf_cmd+=(${TMUX_FZF_OPTIONS})
elif [[ $is_fzf_tmux -eq 1 ]]; then
  fzf_cmd+=(-p -w 62% -h 38%)
else
  fzf_cmd+=(--height=40% --layout=reverse --border)
fi

printf -v preview_cmd '%q %q {}' "$preview_script" "$session"

run_fzf() {
  local header="$1"
  local expect="${2:-}"

  local -a cmd=("${fzf_cmd[@]}")
  cmd+=(--no-multi --delimiter=$'\t' --with-nth=3.. --header="$header")
  if [[ -n "$expect" ]]; then
    cmd+=(--expect="$expect")
  fi
  preview_window="right:60%:wrap"
  if [[ "${TMUX_FZF_PREVIEW:-1}" == "0" ]]; then
    preview_window="${preview_window}:hidden"
  fi
  cmd+=(--bind "ctrl-/:toggle-preview" --preview="$preview_cmd" --preview-window="$preview_window")

  "${cmd[@]}"
}

goto_window() {
  local win="$1"
  tmux select-window -t "${session}:${win}" 2>/dev/null || true
}

goto_pane() {
  local pane_id="$1" # <win>.<pane>
  local win="${pane_id%%.*}"
  goto_window "$win"
  tmux select-pane -t "${session}:${pane_id}" 2>/dev/null || true
}

select_pane_in_window() {
  local win="$1"
  local panes
  panes="$(tmux list-panes -t "${session}:${win}" -F $'P\t#{window_index}.#{pane_index}\t[P] #{window_index}.#{pane_index} [#{pane_title}] #{pane_current_command}  #{pane_current_path} #{?pane_active,[active],[inactive]}' 2>/dev/null || true)"
  [[ -z "$panes" ]] && exit 0

  local out key selected
  out="$(printf '%s\n' "$panes" | run_fzf "Panes in window ${win}  (Enter: switch  Esc: cancel)" "")" || exit 0
  [[ -z "$out" ]] && exit 0

  selected="$out"
  local type id _
  IFS=$'\t' read -r type id _ <<<"$selected"
  [[ "$type" == "P" && -n "$id" ]] && goto_pane "$id"
}

windows="$(tmux list-windows -t "$session" -F $'W\t#{window_index}\t[W] #{window_index}: #{window_name}  (panes #{window_panes})  #{pane_current_path} #{?window_active,[active],}' 2>/dev/null || true)"
[[ -z "$windows" ]] && exit 0

out="$(printf '%s\n' "$windows" | run_fzf "Go to Window  (Enter: switch  Ctrl-p: panes  Ctrl-/: preview)" "ctrl-p")" || exit 0
[[ -z "$out" ]] && exit 0

key=""
selected=""
if [[ "$out" == *$'\n'* ]]; then
  key="${out%%$'\n'*}"
  rest="${out#*$'\n'}"
  selected="${rest%%$'\n'*}"
else
  selected="$out"
fi

[[ -z "$selected" ]] && exit 0

type=""
id=""
IFS=$'\t' read -r type id _ <<<"$selected"

if [[ "$type" == "W" ]]; then
  [[ -z "$id" ]] && exit 0
  if [[ "$key" == "ctrl-p" ]]; then
    select_pane_in_window "$id"
  else
    goto_window "$id"
  fi
  exit 0
fi

exit 0
