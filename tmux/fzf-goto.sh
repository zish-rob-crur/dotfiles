#!/usr/bin/env bash
# prefix+g / prefix+w: jump to a window or pane of the current session.
#
# The list is the session as a tree (goto_entries.py): windows with their
# assistant state, and under them the panes worth telling apart, each with what
# the task board says it is doing. Typing filters cached search metadata; matched panes keep their parent window. The cursor follows
# the best-scoring match, a query of digits alone goes to that window number, and
# clearing the query returns to the current window. Up/down step window to
# window with no query and match to match with one (ctrl-k/ctrl-j always step
# matches); tab/shift-tab cycle through the current window's panes.
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

# The cursor carries the eye: the whole current line is lit and each matched character is a block of
# the terminal's own cyan (palette 6, the Claude status line's model color), so
# it follows the light/dark theme. Unmatched lines keep fzf's default dim, the
# only fade that also reaches the entries' own ANSI spans.
hl='cyan:reverse:bold'
if defaults read -g AppleInterfaceStyle 2>/dev/null | grep -q Dark; then
  theme=dark
  colors="dark,hl:$hl,hl+:$hl,current-bg:#3d484d"
else
  theme=light
  colors="light,hl:$hl,hl+:$hl,current-bg:#dde4ea"
fi
state_dir="$(mktemp -d -t goto-pins)"
trap 'rm -rf "$state_dir"' EXIT
# The theme rides in the command itself: fzf-tmux's popup, where alt-p reloads,
# does not inherit this script's environment.
printf -v refresh '%q ' env GOTO_THEME="$theme" python3 "${script_dir}/goto_entries.py" --client "$current_client" \
  --session "$current_session" --group "${current_group:-$current_session}" \
  --window-id "$current_window_id" --state-dir "$state_dir"
entries="$(eval "$refresh")"
[[ -n "$entries" ]] || exit 0

popup=(-p -w 90% -h 80%)
command -v fzf-tmux >/dev/null 2>&1 || { tmux display-message "fzf-tmux not found"; exit 0; }
printf -v step '%q' "${script_dir}/goto_step.sh"
printf -v map '%q' "${state_dir}/windows"
printf -v search '%q ' python3 "${script_dir}/goto_search.py" --state-dir "$state_dir" --window-id "$current_window_id"
printf -v preview "printf '%%s\\n\\n' %s; tmux capture-pane -ep -S -60 -t %s" '{12}' '{6}'
out="$(fzf-tmux "${popup[@]}" -- --ansi --disabled --no-sort --track --id-nth=1,2 --layout=reverse --no-multi \
  --color="$colors" --highlight-line \
  --delimiter=$'\t' --with-nth=3 --expect=ctrl-o,ctrl-g \
  --header="Enter: go  ·  ↑↓: windows  ·  tab: panes  ·  ctrl-o: focus only  ·  ctrl-g: this client  ·  ctrl-/: preview  ·  alt-p: pin" \
  --bind "change:reload($search --query {q})" \
  --bind "alt-p:reload($refresh --toggle-pin {5} >/dev/null && $search --query {q})" \
  --preview="$preview" --preview-window='right:50%:hidden' --bind 'ctrl-/:toggle-preview' \
  --bind "result:transform:${script_dir}/goto_cursor.sh {q} '${state_dir}/start' '${state_dir}/windows'" \
  --bind "up:transform:$step window up {q} {n} $map" --bind "down:transform:$step window down {q} {n} $map" \
  --bind "tab:transform:$step pane next {q} {n} $map" --bind "btab:transform:$step pane prev {q} {n} $map" \
  --bind 'ctrl-k:up-match,ctrl-j:down-match' <<<"$entries")" || exit 0

key="${out%%$'\n'*}"
selected="${out#*$'\n'}"
[[ -n "$selected" && "$selected" != "$out" ]] || exit 0

IFS=$'\t' read -r _type _id _display state window_id pane_id target_session target_client target_tty _index _pane_index _search <<<"$selected"
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
