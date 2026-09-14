#!/usr/bin/env bash
# prefix+g / prefix+w: jump to a window or pane of the current session.
#
# The list is the session as a tree (goto_entries.py): windows with their
# assistant state, and under them the panes worth telling apart, each with what
# the task board says it is doing. Typing filters; non-matching lines stay
# dimmed so a matched pane is still seen under its window.
#
# Enter on a window or pane already shown by another Ghostty client of the same
# session group focuses that Ghostty window instead of switching this client.
#   ctrl-o  focus that Ghostty window without selecting the pane
#   ctrl-g  always switch this client
#   ctrl-/  toggle the preview of the pane's screen
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
focus_script="${script_dir}/focus-ghostty-window.sh"

current_client="" current_session="" current_group="" current_window_id=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --client) current_client="${2:-}"; shift 2 ;;
    --session) current_session="${2:-}"; shift 2 ;;
    --session-group) current_group="${2:-}"; shift 2 ;;
    --window-id) current_window_id="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

if [[ -z "$current_session" || -z "$current_client" || -z "$current_window_id" ]]; then
  IFS=$'\t' read -r detected_client detected_session detected_group detected_window_id \
    <<<"$(tmux display-message -p '#{client_name}'$'\t''#{session_name}'$'\t''#{session_group}'$'\t''#{window_id}' 2>/dev/null || true)"
  current_client="${current_client:-${detected_client:-}}"
  current_session="${current_session:-${detected_session:-}}"
  current_group="${current_group:-${detected_group:-}}"
  current_window_id="${current_window_id:-${detected_window_id:-}}"
fi
[[ -n "$current_session" ]] || exit 0

entries="$(python3 "${script_dir}/goto_entries.py" --client "$current_client" --session "$current_session" \
  --group "${current_group:-$current_session}" --window-id "$current_window_id")"
[[ -n "$entries" ]] || exit 0
start="$(awk -F'\t' -v w="$current_window_id" '$1 == "W" && $5 == w { print NR; exit }' <<<"$entries")"

popup=(-p -w 90% -h 80%)
command -v fzf-tmux >/dev/null 2>&1 || { tmux display-message "fzf-tmux not found"; exit 0; }
printf -v preview "tmux capture-pane -ep -S -60 -t %s" '{6}'
out="$(fzf-tmux "${popup[@]}" -- --ansi --raw --no-sort --layout=reverse --no-multi \
  --delimiter=$'\t' --with-nth=3 --expect=ctrl-o,ctrl-g \
  --header="Enter: go  ·  ctrl-o: focus only  ·  ctrl-g: this client  ·  ctrl-/: preview" \
  --preview="$preview" --preview-window='right:50%:hidden' --bind 'ctrl-/:toggle-preview' \
  --bind "load:pos(${start:-1})" <<<"$entries")" || exit 0

key="${out%%$'\n'*}"
selected="${out#*$'\n'}"
[[ -n "$selected" && "$selected" != "$out" ]] || exit 0

IFS=$'\t' read -r _type _id _display state window_id pane_id target_session target_client target_tty _index _pane_index <<<"$selected"
[[ "$target_tty" == "-" ]] && target_tty=""

# A window line carries its active pane, or the one pane it stands for, so
# selecting pane_id is right for both kinds of line.
switch_here() {
  tmux select-window -t "${current_session}:${window_id}" 2>/dev/null || true
  tmux select-pane -t "$pane_id" 2>/dev/null || true
}

focus_ghostty() {
  [[ -x "$focus_script" ]] || return 1
  "$focus_script" --session "$target_session" --client "$target_client" --tty "$target_tty" \
    --window-id "$window_id" --pane-id "$pane_id" >/dev/null 2>&1
}

if [[ "$key" == "ctrl-g" || "$state" != "VISIBLE" ]]; then
  switch_here
elif [[ "$key" == "ctrl-o" ]]; then
  focus_ghostty || switch_here
else
  tmux select-pane -t "$pane_id" 2>/dev/null || true
  focus_ghostty || switch_here
fi
