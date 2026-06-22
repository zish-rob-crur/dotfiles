#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
preview_script="${script_dir}/fzf-goto-preview.sh"
focus_script="${script_dir}/focus-ghostty-window.sh"

current_client=""
current_session=""
current_group=""
current_window_id=""
current_pane_id=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --client)
      current_client="${2:-}"
      shift 2
      ;;
    --session)
      current_session="${2:-}"
      shift 2
      ;;
    --session-group)
      current_group="${2:-}"
      shift 2
      ;;
    --window-id)
      current_window_id="${2:-}"
      shift 2
      ;;
    --pane-id)
      current_pane_id="${2:-}"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -z "$current_session" || -z "$current_client" || -z "$current_window_id" || -z "$current_pane_id" ]]; then
  current_context="$(tmux display-message -p '#{client_name}'$'\t''#{session_name}'$'\t''#{session_group}'$'\t''#{window_id}'$'\t''#{pane_id}' 2>/dev/null || true)"
  if [[ -n "$current_context" ]]; then
    IFS=$'\t' read -r detected_client detected_session detected_group detected_window_id detected_pane_id <<<"$current_context"
    current_client="${current_client:-$detected_client}"
    current_session="${current_session:-$detected_session}"
    current_group="${current_group:-$detected_group}"
    current_window_id="${current_window_id:-$detected_window_id}"
    current_pane_id="${current_pane_id:-$detected_pane_id}"
  fi
fi

if [[ -z "$current_session" ]]; then
  exit 0
fi

current_group="${current_group:-$current_session}"

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

printf -v preview_cmd '%q %q {}' "$preview_script" "$current_session"

cleaned_value=""
clean_field() {
  cleaned_value="${1//$'\t'/ }"
  cleaned_value="${cleaned_value//$'\n'/ }"
}

is_ghostty_client() {
  local termname="$1"
  local termtype="$2"
  case "${termname} ${termtype}" in
    *[Gg][Hh][Oo][Ss][Tt][Tt][Yy]*) return 0 ;;
    *) return 1 ;;
  esac
}

same_group_as_current() {
  local session="$1"
  local group="$2"
  if [[ -n "$group" && "$group" == "$current_group" ]]; then
    return 0
  fi
  [[ -z "$group" && "$session" == "$current_session" ]]
}

visible_clients=""
all_clients="$(tmux list-clients -F '#{client_name}'$'\t''#{client_tty}'$'\t''#{client_termname}'$'\t''#{client_termtype}'$'\t''#{session_name}'$'\t''#{session_group}'$'\t''#{window_id}'$'\t''#{pane_id}'$'\t''#{window_index}'$'\t''#{window_name}' 2>/dev/null || true)"
while IFS=$'\t' read -r client tty termname termtype session group window_id pane_id window_index window_name; do
  [[ -z "$client" ]] && continue
  [[ "$client" == "$current_client" ]] && continue
  same_group_as_current "$session" "$group" || continue
  is_ghostty_client "$termname" "$termtype" || continue

  clean_field "$window_name"
  printf -v line '%s\t%s\t%s\t%s\t%s\t%s\t%s' \
    "$session" "$client" "$tty" "$window_id" "$pane_id" "$window_index" "$cleaned_value"
  if [[ -n "$visible_clients" ]]; then
    visible_clients+=$'\n'
  fi
  visible_clients+="$line"
done <<<"$all_clients"

visible_client_for_window() {
  local needle_window_id="$1"
  local session client tty window_id pane_id window_index window_name

  [[ -z "$visible_clients" ]] && return 1
  while IFS=$'\t' read -r session client tty window_id pane_id window_index window_name; do
    if [[ "$window_id" == "$needle_window_id" ]]; then
      printf '%s\t%s\t%s\t%s\t%s\t%s' "$session" "$client" "$tty" "$pane_id" "$window_index" "$window_name"
      return 0
    fi
  done <<<"$visible_clients"
  return 1
}

visible_target_session=""
visible_target_client=""
visible_target_tty=""
visible_target_pane_id=""
visible_target_window_index=""
visible_target_window_name=""
set_visible_client_for_window() {
  local needle_window_id="$1"
  local session client tty window_id pane_id window_index window_name

  visible_target_session=""
  visible_target_client=""
  visible_target_tty=""
  visible_target_pane_id=""
  visible_target_window_index=""
  visible_target_window_name=""

  [[ -z "$visible_clients" ]] && return 1
  while IFS=$'\t' read -r session client tty window_id pane_id window_index window_name; do
    if [[ "$window_id" == "$needle_window_id" ]]; then
      visible_target_session="$session"
      visible_target_client="$client"
      visible_target_tty="$tty"
      visible_target_pane_id="$pane_id"
      visible_target_window_index="$window_index"
      visible_target_window_name="$window_name"
      return 0
    fi
  done <<<"$visible_clients"
  return 1
}

run_fzf() {
  local header="$1"
  local expect="${2:-}"

  local -a cmd=("${fzf_cmd[@]}")
  cmd+=(--no-multi --delimiter=$'\t' --with-nth=3 --header="$header")
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

visible_entries=""
current_entries=""
normal_entries=""

append_entry() {
  local state="$1"
  local line="$2"

  case "$state" in
    VISIBLE)
      if [[ -n "$visible_entries" ]]; then visible_entries+=$'\n'; fi
      visible_entries+="$line"
      ;;
    CURRENT)
      if [[ -n "$current_entries" ]]; then current_entries+=$'\n'; fi
      current_entries+="$line"
      ;;
    *)
      if [[ -n "$normal_entries" ]]; then normal_entries+=$'\n'; fi
      normal_entries+="$line"
      ;;
  esac
}

entry=""
make_entry() {
  local type="$1"
  local id="$2"
  local display="$3"
  local state="$4"
  local window_id="$5"
  local pane_id="$6"
  local target_session="$7"
  local target_client="$8"
  local target_tty="$9"
  shift 9
  local window_index="$1"
  local pane_index="${2:-}"

  clean_field "$display"
  printf -v entry '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' \
    "$type" "$id" "$cleaned_value" "$state" "$window_id" "$pane_id" \
    "$target_session" "$target_client" "$target_tty" "$window_index" "$pane_index"
}

window_state=""
classify_window() {
  local window_id="$1"
  if [[ "$window_id" == "$current_window_id" ]]; then
    window_state="CURRENT"
  elif set_visible_client_for_window "$window_id"; then
    window_state="VISIBLE"
  else
    window_state="NORMAL"
  fi
}

target_session=""
target_client=""
target_tty=""
target_pane_id=""
target_window_index=""
target_window_name=""
set_window_target_meta() {
  local state="$1"
  local window_id="$2"
  local default_pane_id="$3"

  if [[ "$state" == "VISIBLE" ]]; then
    if set_visible_client_for_window "$window_id"; then
      target_session="$visible_target_session"
      target_client="$visible_target_client"
      target_tty="$visible_target_tty"
      target_pane_id="$visible_target_pane_id"
      target_window_index="$visible_target_window_index"
      target_window_name="$visible_target_window_name"
      return 0
    fi
  fi

  target_session="$current_session"
  target_client="$current_client"
  target_tty=""
  target_pane_id="$default_pane_id"
  target_window_index=""
  target_window_name=""
}

state_label=""
label_for_state() {
  local state="$1"
  local target_session="$2"
  local target_client="$3"
  local target_tty="$4"

  case "$state" in
    VISIBLE) state_label="[Ghostty: ${target_session} ${target_tty:-$target_client}]" ;;
    CURRENT) state_label="[current]" ;;
    *) state_label="" ;;
  esac
}

goto_window() {
  local window_id="$1"
  tmux select-window -t "${current_session}:${window_id}" 2>/dev/null || true
}

goto_pane() {
  local window_id="$1"
  local pane_id="$2"
  goto_window "$window_id"
  tmux select-pane -t "$pane_id" 2>/dev/null || true
}

focus_ghostty() {
  local target_session="$1"
  local target_client="$2"
  local target_tty="$3"
  local window_id="$4"
  local pane_id="$5"

  [[ -x "$focus_script" ]] || return 1
  "$focus_script" \
    --session "$target_session" \
    --client "$target_client" \
    --tty "$target_tty" \
    --window-id "$window_id" \
    --pane-id "$pane_id" >/dev/null 2>&1
}

handle_selection() {
  local key="$1"
  local selected="$2"
  local type id display state window_id pane_id target_session target_client target_tty window_index pane_index

  IFS=$'\t' read -r type id display state window_id pane_id target_session target_client target_tty window_index pane_index <<<"$selected"
  [[ -z "$type" || -z "$window_id" ]] && exit 0

  if [[ "$type" == "W" && "$key" == "ctrl-p" ]]; then
    select_pane_in_window "$window_id" "$state" "$target_session" "$target_client" "$target_tty"
    exit 0
  fi

  if [[ "$key" == "ctrl-g" ]]; then
    if [[ "$type" == "P" ]]; then
      goto_pane "$window_id" "$pane_id"
    else
      goto_window "$window_id"
    fi
    exit 0
  fi

  if [[ "$state" == "VISIBLE" ]]; then
    if [[ "$key" == "ctrl-o" ]]; then
      focus_ghostty "$target_session" "$target_client" "$target_tty" "$window_id" "$pane_id" && exit 0
      goto_window "$window_id"
      exit 0
    fi

    if [[ "$type" == "P" && -n "$pane_id" ]]; then
      tmux select-pane -t "$pane_id" 2>/dev/null || true
    fi

    focus_ghostty "$target_session" "$target_client" "$target_tty" "$window_id" "$pane_id" && exit 0

    if [[ "$type" == "P" ]]; then
      goto_pane "$window_id" "$pane_id"
    else
      goto_window "$window_id"
    fi
    exit 0
  fi

  if [[ "$state" == "CURRENT" && "$type" == "W" ]]; then
    tmux refresh-client -S 2>/dev/null || true
    exit 0
  fi

  if [[ "$type" == "P" ]]; then
    goto_pane "$window_id" "$pane_id"
  elif [[ "$type" == "W" ]]; then
    goto_window "$window_id"
  fi
  exit 0
}

select_pane_in_window() {
  local window_id="$1"
  local inherited_state="$2"
  local inherited_session="$3"
  local inherited_client="$4"
  local inherited_tty="$5"
  local panes entries out key selected

  panes="$(tmux list-panes -t "${current_session}:${window_id}" -F '#{pane_id}'$'\t''#{pane_index}'$'\t''#{pane_title}'$'\t''#{pane_current_command}'$'\t''#{pane_current_path}'$'\t''#{pane_active}'$'\t''#{window_index}'$'\t''#{window_name}' 2>/dev/null || true)"
  [[ -z "$panes" ]] && exit 0

  entries=""
  while IFS=$'\t' read -r pane_id pane_index pane_title pane_command pane_path pane_active window_index window_name; do
    [[ -z "$pane_id" ]] && continue
    local marker target_session target_client target_tty label display entry clean_window_name clean_pane_title clean_pane_path

    marker=""
    [[ "$pane_active" == "1" ]] && marker="[active]"
    target_session="$current_session"
    target_client="$current_client"
    target_tty=""

    if [[ "$inherited_state" == "VISIBLE" ]]; then
      target_session="$inherited_session"
      target_client="$inherited_client"
      target_tty="$inherited_tty"
    fi

    label_for_state "$inherited_state" "$target_session" "$target_client" "$target_tty"
    label="$state_label"
    clean_field "$window_name"; clean_window_name="$cleaned_value"
    clean_field "$pane_title"; clean_pane_title="$cleaned_value"
    clean_field "$pane_path"; clean_pane_path="$cleaned_value"
    printf -v display '%-7s %s.%s: %s  [%s] %s  %s %s %s' \
      "$inherited_state" "$window_index" "$pane_index" "$clean_window_name" \
      "$clean_pane_title" "$pane_command" "$clean_pane_path" "$marker" "$label"
    make_entry "P" "$pane_id" "$display" "$inherited_state" "$window_id" "$pane_id" \
      "$target_session" "$target_client" "$target_tty" "$window_index" "$pane_index"
    if [[ -n "$entries" ]]; then entries+=$'\n'; fi
    entries+="$entry"
  done <<<"$panes"

  out="$(printf '%s\n' "$entries" | run_fzf "Panes in window ${window_id}  (Enter: switch  Ctrl-o: focus only  Ctrl-g: local  Esc: cancel)" "ctrl-o,ctrl-g")" || exit 0
  [[ -z "$out" ]] && exit 0

  key=""
  selected=""
  if [[ "$out" == *$'\n'* ]]; then
    key="${out%%$'\n'*}"
    selected="${out#*$'\n'}"
    selected="${selected%%$'\n'*}"
  else
    selected="$out"
  fi
  [[ -z "$selected" ]] && exit 0

  handle_selection "$key" "$selected"
}

windows="$(tmux list-windows -t "$current_session" -F '#{window_id}'$'\t''#{window_index}'$'\t''#{window_name}'$'\t''#{window_panes}'$'\t''#{pane_id}'$'\t''#{pane_current_path}'$'\t''#{window_active}' 2>/dev/null || true)"
while IFS=$'\t' read -r window_id window_index window_name window_panes pane_id pane_path window_active; do
  [[ -z "$window_id" ]] && continue
  classify_window "$window_id"
  state="$window_state"
  set_window_target_meta "$state" "$window_id" "$pane_id"
  target_pane_id="${target_pane_id:-$pane_id}"
  label_for_state "$state" "$target_session" "$target_client" "$target_tty"
  label="$state_label"
  active_marker=""
  [[ "$window_active" == "1" ]] && active_marker="[active]"

  clean_field "$window_name"; clean_window_name="$cleaned_value"
  clean_field "$pane_path"; clean_pane_path="$cleaned_value"
  printf -v display '%-7s %s %s: %s  (panes %s)  %s %s %s' \
    "$state" "$window_id" "$window_index" "$clean_window_name" \
    "$window_panes" "$clean_pane_path" "$active_marker" "$label"
  make_entry "W" "$window_id" "$display" "$state" "$window_id" "$target_pane_id" \
    "$target_session" "$target_client" "$target_tty" "$window_index" ""
  append_entry "$state" "$entry"
done <<<"$windows"

panes="$(tmux list-panes -s -t "$current_session" -F '#{window_id}'$'\t''#{window_index}'$'\t''#{window_name}'$'\t''#{pane_id}'$'\t''#{pane_index}'$'\t''#{pane_title}'$'\t''#{pane_current_command}'$'\t''#{pane_current_path}'$'\t''#{pane_active}' 2>/dev/null || true)"
while IFS=$'\t' read -r window_id window_index window_name pane_id pane_index pane_title pane_command pane_path pane_active; do
  [[ -z "$window_id" || -z "$pane_id" ]] && continue
  classify_window "$window_id"
  state="$window_state"
  set_window_target_meta "$state" "$window_id" "$pane_id"
  label_for_state "$state" "$target_session" "$target_client" "$target_tty"
  label="$state_label"
  active_marker=""
  [[ "$pane_active" == "1" ]] && active_marker="[active]"

  clean_field "$window_name"; clean_window_name="$cleaned_value"
  clean_field "$pane_title"; clean_pane_title="$cleaned_value"
  clean_field "$pane_path"; clean_pane_path="$cleaned_value"
  printf -v display '%-7s %s.%s: %s  [%s] %s  %s %s %s' \
    "$state" "$window_index" "$pane_index" "$clean_window_name" \
    "$clean_pane_title" "$pane_command" "$clean_pane_path" "$active_marker" "$label"
  make_entry "P" "$pane_id" "$display" "$state" "$window_id" "$pane_id" \
    "$target_session" "$target_client" "$target_tty" "$window_index" "$pane_index"
  append_entry "$state" "$entry"
done <<<"$panes"

entries=""
for block in "$visible_entries" "$current_entries" "$normal_entries"; do
  [[ -z "$block" ]] && continue
  if [[ -n "$entries" ]]; then entries+=$'\n'; fi
  entries+="$block"
done
[[ -z "$entries" ]] && exit 0

out="$(printf '%s\n' "$entries" | run_fzf "Go to Window/Pane  (Enter: goto  Ctrl-p: panes  Ctrl-o: focus only  Ctrl-g: local  Ctrl-/: preview)" "ctrl-p,ctrl-o,ctrl-g")" || exit 0
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
handle_selection "$key" "$selected"
