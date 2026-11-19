#!/usr/bin/env bash
set -euo pipefail

export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$HOME/.local/bin"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
base_dir="$PWD"
target_pane="${FASTCOPY_TARGET_PANE_ID:-${TMUX_PANE:-}}"
requested_path=""
direct_action=""
fastcopy_mode=0
open_popup_mode=0

usage() {
  cat <<'EOF'
Usage: path-actions.sh [options] [path]

Select a file or directory, then copy its path or open it with Neovim or Finder.

Options:
  --cwd DIR          Resolve relative paths from DIR
  --pane PANE_ID     Split from this tmux pane
  --fastcopy         Copy selection from stdin/arg, then open action popup if path exists
  -a, --absolute     Copy the absolute path without showing the action menu
  -r, --relative     Copy the path relative to --cwd without showing the menu
  -f, --finder       Copy a Finder file object without showing the menu (macOS)
  -h, --help         Show this help
EOF
}

fail() {
  local message="$1"
  if [[ -n "${TMUX:-}" ]] && command -v tmux >/dev/null 2>&1; then
    tmux display-message -- "cpf: ${message}"
  else
    printf 'cpf: %s\n' "$message" >&2
  fi
  exit 1
}

notify() {
  local message="$1"
  if [[ -n "${TMUX:-}" ]] && command -v tmux >/dev/null 2>&1; then
    tmux display-message -- "$message"
  else
    printf '%s\n' "$message"
  fi
}

while (( $# > 0 )); do
  case "$1" in
    --cwd)
      (( $# >= 2 )) || fail "--cwd requires a directory"
      base_dir="$2"
      shift 2
      ;;
    --pane)
      (( $# >= 2 )) || fail "--pane requires a pane id"
      target_pane="$2"
      shift 2
      ;;
    --fastcopy)
      fastcopy_mode=1
      shift
      ;;
    --open-popup)
      open_popup_mode=1
      shift
      ;;
    -a|--absolute)
      direct_action="copy_absolute"
      shift
      ;;
    -r|--relative)
      direct_action="copy_relative"
      shift
      ;;
    -f|--finder)
      direct_action="copy_finder"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      if (( $# > 0 )); then
        requested_path="$1"
        shift
      fi
      break
      ;;
    -*)
      fail "unknown option: $1"
      ;;
    *)
      [[ -z "$requested_path" ]] || fail "only one path can be selected"
      requested_path="$1"
      shift
      ;;
  esac
done

(( $# == 0 )) || fail "only one path can be selected"
[[ -d "$base_dir" ]] || fail "directory does not exist: $base_dir"
base_dir="$(cd "$base_dir" && pwd -P)"
cd "$base_dir"

list_paths() {
  printf '.\n'
  if command -v fd >/dev/null 2>&1; then
    fd --hidden --color=never \
      --exclude .git --exclude node_modules --exclude .venv --exclude venv \
      . 2>/dev/null
  elif command -v fdfind >/dev/null 2>&1; then
    fdfind --hidden --color=never \
      --exclude .git --exclude node_modules --exclude .venv --exclude venv \
      . 2>/dev/null
  else
    find . \
      \( -name .git -o -name node_modules -o -name .venv -o -name venv \) -prune \
      -o -mindepth 1 -print | sed 's#^\./##'
  fi
}

preview_script='
case "$target" in
  http://*|https://*)
    printf "URL: %s\n\n" "$target"
    curl -sSI --max-time 2 "$target" 2>/dev/null || true
    exit 0
    ;;
esac
if [ -d "$target" ]; then
  if command -v eza >/dev/null 2>&1; then
    eza --all --color=always --icons=always --group-directories-first "$target"
  elif command -v tree >/dev/null 2>&1; then
    tree -C -L 2 "$target" | head -200
  else
    find "$target" -maxdepth 2 -print | head -200
  fi
elif command -v bat >/dev/null 2>&1; then
  bat --style=numbers,changes --color=always --line-range=:500 -- "$target"
else
  file -- "$target"
  sed -n "1,200p" -- "$target" 2>/dev/null
fi'

choose_path() {
  command -v fzf >/dev/null 2>&1 || fail "fzf is not installed"

  list_paths | FZF_DEFAULT_OPTS="" fzf \
    --layout=reverse --border --height=100% \
    --prompt='Path > ' --pointer='▶' \
    --bind='ctrl-d:preview-half-page-down,ctrl-u:preview-half-page-up' \
    --preview="target={}; $preview_script" --preview-window='right:65%:wrap'
}

copy_text() {
  local value="$1"
  [[ -x "$script_dir/copy-to-clipboard.sh" ]] || fail "clipboard helper is not executable"
  printf '%s' "$value" | "$script_dir/copy-to-clipboard.sh"
}

is_http_url() {
  [[ "$1" =~ ^https?:// ]]
}

resolve_single_candidate() {
  local candidate="$1"
  if is_http_url "$candidate"; then
    printf '%s\n' "$candidate"
    return 0
  fi
  if [[ "$candidate" == "~" ]]; then
    candidate="$HOME"
  elif [[ "$candidate" == "~/"* ]]; then
    candidate="$HOME/${candidate#\~/}"
  fi
  if [[ -e "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  local git_root
  if [[ "$candidate" != /* ]] && git_root="$(git -C "$base_dir" rev-parse --show-toplevel 2>/dev/null)" && [[ -e "$git_root/$candidate" ]]; then
    printf '%s\n' "$git_root/$candidate"
    return 0
  fi
  return 1
}

resolve_existing_path() {
  local raw="$1" variant
  if [[ "$raw" == *$'\n'* || "$raw" == *$'\r'* ]]; then
    for variant in \
      "$(printf '%s' "$raw" | perl -0777 -pe 's/[ \t]*\r?\n[ \t]*//g')" \
      "$(printf '%s' "$raw" | perl -0777 -pe 's#(?<=/)[ \t]*\r?\n[ \t]*##g; s/[ \t]*\r?\n[ \t]*/ /g')"; do
      if resolve_single_candidate "$variant"; then
        return 0
      fi
    done
    return 1
  fi
  resolve_single_candidate "$raw"
}

recover_wrapped_path() {
  local raw="$1"
  [[ -n "$target_pane" ]] && command -v tmux >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1 || return 1
  local pane_text
  pane_text="$(tmux capture-pane -p -t "$target_pane" 2>/dev/null || true)"
  [[ -n "$pane_text" ]] || return 1
  python3 - "$raw" "$base_dir" "$pane_text" <<'PY'
import os, re, sys

raw, base_dir, pane_text = sys.argv[1], sys.argv[2], sys.argv[3]
clean = re.sub(r"[ \t]*\r?\n[ \t]*", "", raw).rstrip("/")
if not clean:
    sys.exit(1)

pattern = re.compile(re.escape(clean) + r"(/?)[ \t│┃╎╏▐▍⌄^]*\r?\n[ \t]*([A-Za-z0-9._-]+(?:[ /][A-Za-z0-9._-]+)*)")
for m in pattern.finditer(pane_text):
    sep, tail = m.group(1), m.group(2)
    candidates = [f"{clean}/{tail}", f"{clean} {tail}", f"{clean}{tail}"] if not sep else [f"{clean}/{tail}", f"{clean}{tail}"]
    for joined in candidates:
        expanded = os.path.expanduser(joined)
        if not os.path.isabs(expanded):
            expanded = os.path.join(base_dir, expanded)
        if os.path.exists(expanded):
            print(joined)
            sys.exit(0)
sys.exit(1)
PY
}

if (( open_popup_mode )); then
  [[ -n "$requested_path" && -n "$target_pane" ]] || exit 0
  session_name="$(tmux display-message -p -t "$target_pane" '#{session_name}' 2>/dev/null || true)"
  client="$(tmux list-clients -F '#{pane_id} #{session_name} #{client_name}' 2>/dev/null | awk -v p="$target_pane" -v s="$session_name" '$1 == p { print $3; found=1; exit } $2 == s && !fallback { fallback=$3 } END { if (!found && fallback) print fallback }')"
  printf -v popup_cmd 'exec %q --cwd %q --pane %q -- %q' \
    "$script_dir/path-actions.sh" "$base_dir" "$target_pane" "$requested_path"
  popup_args=(display-popup -E -T "Path actions (Esc: keep copy)" -w 90% -h 80% -d "$base_dir")
  [[ -z "$client" ]] || popup_args+=(-c "$client")
  popup_args+=(-t "$target_pane" "$popup_cmd")
  tmux "${popup_args[@]}"
  exit 0
fi

if (( fastcopy_mode )); then
  [[ -n "$requested_path" ]] || requested_path="$(cat)"
  [[ -n "$requested_path" ]] || exit 0
  if [[ "$requested_path" == *$'\n'* || "$requested_path" == *$'\r'* ]]; then
    for unwrapped in \
      "$(printf '%s' "$requested_path" | perl -0777 -pe 's/[ \t]*\r?\n[ \t]*//g')" \
      "$(printf '%s' "$requested_path" | perl -0777 -pe 's#(?<=/)[ \t]*\r?\n[ \t]*##g; s/[ \t]*\r?\n[ \t]*/ /g')"; do
      if resolve_single_candidate "$unwrapped" >/dev/null 2>&1; then
        requested_path="$unwrapped"
        break
      fi
    done
  fi
  resolved_path="$(resolve_existing_path "$requested_path" || true)"
  if [[ -z "$resolved_path" || -d "$resolved_path" ]]; then
    if wrapped_candidate="$(recover_wrapped_path "$requested_path" 2>/dev/null)"; then
      requested_path="$wrapped_candidate"
      resolved_path="$(resolve_existing_path "$requested_path" || true)"
    fi
  fi
  if command -v tmux >/dev/null 2>&1; then
    printf '%s' "$requested_path" | tmux load-buffer -w - 2>/dev/null || true
  fi
  copy_text "$requested_path" 2>/dev/null || true
  [[ -n "$resolved_path" ]] || exit 0
  if [[ -n "$target_pane" ]] && command -v tmux >/dev/null 2>&1; then
    printf -v run_cmd '%q --open-popup --cwd %q --pane %q -- %q' \
      "$script_dir/path-actions.sh" "$base_dir" "$target_pane" "$resolved_path"
    run_cmd="${run_cmd//#/##}"
    tmux run-shell -b -d 0.08 "$run_cmd" >/dev/null 2>&1 || true
  fi
  exit 0
fi

if [[ -z "$requested_path" ]]; then
  requested_path="$(choose_path)" || exit 0
fi

requested_path="$(resolve_existing_path "$requested_path")" || fail "path does not exist: $requested_path"
if is_http_url "$requested_path"; then
  absolute_path="$requested_path"
  relative_path="$requested_path"
elif command -v python3 >/dev/null 2>&1; then
  absolute_path="$(realpath "$requested_path")"
  relative_path="$(python3 - "$absolute_path" "$base_dir" <<'PY'
import os
import sys

print(os.path.relpath(sys.argv[1], sys.argv[2]))
PY
)"
else
  absolute_path="$(realpath "$requested_path")"
  if [[ "$absolute_path" == "$base_dir" ]]; then
    relative_path="."
  elif [[ "$absolute_path" == "$base_dir/"* ]]; then
    relative_path="${absolute_path#"$base_dir/"}"
  else
    relative_path="$absolute_path"
  fi
fi

copy_finder_file() {
  command -v osascript >/dev/null 2>&1 || fail "Finder file copy is only available on macOS"
  osascript - "$absolute_path" >/dev/null <<'APPLESCRIPT'
on run argv
  set the clipboard to POSIX file (item 1 of argv)
end run
APPLESCRIPT
}

require_tmux_nvim() {
  [[ -n "${TMUX:-}" ]] || fail "this action must run inside tmux"
  command -v tmux >/dev/null 2>&1 || fail "tmux is not installed"
  command -v nvim >/dev/null 2>&1 || fail "nvim is not installed"
}

editor_working_dir="$absolute_path"
[[ -d "$editor_working_dir" ]] || editor_working_dir="$(dirname "$absolute_path")"

find_window_nvim_pane() {
  local target_window=""
  [[ -n "${TMUX:-}" ]] && command -v tmux >/dev/null 2>&1 || return 1
  if [[ -n "$target_pane" ]]; then
    target_window="$(tmux display-message -p -t "$target_pane" '#{window_id}' 2>/dev/null || true)"
  fi
  local -a list_args=(list-panes -F $'#{pane_id}\t#{pane_current_command}')
  [[ -z "$target_window" ]] || list_args+=(-t "$target_window")
  local pane_id
  pane_id="$(tmux "${list_args[@]}" 2>/dev/null | awk -F'\t' '$2 ~ /^(n?vim|view|vimdiff)$/ { print $1; exit }')"
  [[ -n "$pane_id" ]] || return 1
  printf '%s\n' "$pane_id"
}

open_in_current_window_nvim() {
  local nvim_pane escaped_vim_path vim_cmd
  require_tmux_nvim
  if ! nvim_pane="$(find_window_nvim_pane)"; then
    open_in_new_pane
    return
  fi
  escaped_vim_path="${absolute_path//\'/\'\'}"
  vim_cmd=":if &buftype !=# '' || !&modifiable | wincmd w | endif | execute 'edit ' . fnameescape('${escaped_vim_path}')"
  tmux send-keys -t "$nvim_pane" C-\\ C-n Escape
  tmux send-keys -t "$nvim_pane" -l "$vim_cmd"
  tmux send-keys -t "$nvim_pane" Enter
  tmux select-pane -t "$nvim_pane"
}

open_in_new_pane() {
  local nvim_bin editor_command split_target="" split_flag="-h"
  local -a list_args tmux_args
  require_tmux_nvim
  if [[ -z "$target_pane" ]]; then
    target_pane="$(tmux display-message -p '#{pane_id}' 2>/dev/null || true)"
  fi
  list_args=(list-panes -F $'#{pane_id}\t#{pane_width}\t#{pane_height}')
  [[ -z "$target_pane" ]] || list_args+=(-t "$target_pane")
  # Split the largest pane in the window: side-by-side when wide (width > 2.5 * height), stacked otherwise.
  read -r split_target split_flag < <(
    tmux "${list_args[@]}" 2>/dev/null | awk -F'\t' '
      $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/ {
        area = $2 * $3
        if (area > best_area) {
          best_area = area
          best_pane = $1
          best_flag = ($2 * 2 > $3 * 5) ? "-h" : "-v"
        }
      }
      END {
        if (best_pane) print best_pane, best_flag
      }
    '
  ) || true
  split_target="${split_target:-$target_pane}"
  nvim_bin="$(command -v nvim)"
  printf -v editor_command 'exec %q -- %q' "$nvim_bin" "$absolute_path"
  tmux_args=(split-window "$split_flag" -c "$editor_working_dir")
  [[ -z "$split_target" ]] || tmux_args+=(-t "$split_target")
  tmux "${tmux_args[@]}" "$editor_command"
}

open_in_new_window() {
  local nvim_bin editor_command target_window=""
  local -a tmux_args
  require_tmux_nvim
  if [[ -n "$target_pane" ]]; then
    target_window="$(tmux display-message -p -t "$target_pane" '#{window_id}' 2>/dev/null || true)"
  fi
  nvim_bin="$(command -v nvim)"
  printf -v editor_command 'exec %q -- %q' "$nvim_bin" "$absolute_path"
  tmux_args=(new-window -c "$editor_working_dir")
  [[ -z "$target_window" ]] || tmux_args+=(-a -t "$target_window")
  tmux "${tmux_args[@]}" "$editor_command"
}

is_html_file() {
  [[ -f "$absolute_path" ]] && [[ "$absolute_path" == *.[hH][tT][mM][lL] || "$absolute_path" == *.[hH][tT][mM] ]]
}

open_in_browser() {
  command -v open >/dev/null 2>&1 || fail "open is only available on macOS"
  open "$absolute_path"
}

choose_action() {
  local header target_quoted
  header="$(printf 'Relative: %s\nAbsolute: %s\nCtrl-d/u: scroll preview' "$relative_path" "$absolute_path")"
  printf -v target_quoted '%q' "$absolute_path"
  local -a actions=()
  if is_http_url "$absolute_path"; then
    actions=(
      'open in browser'
      'copy absolute path'
    )
  else
    if is_html_file; then
      actions+=('open in browser')
    fi
    if find_window_nvim_pane >/dev/null 2>&1; then
      actions+=(
        'replace in current window nvim'
        'new tmux pane + nvim'
        'new tmux window + nvim'
        'copy absolute path'
        'copy relative path'
        'copy as Finder file'
      )
    else
      actions+=(
        'new tmux pane + nvim'
        'new tmux window + nvim'
        'copy absolute path'
        'copy relative path'
        'copy as Finder file'
      )
    fi
    if [[ "$OSTYPE" == darwin* ]]; then
      actions+=('open with Finder')
    fi
  fi
  printf '%s\n' "${actions[@]}" |
    FZF_DEFAULT_OPTS="" fzf \
      --layout=reverse --border --height=100% \
      --prompt='Action > ' --pointer='▶' --header="$header" \
      --bind='ctrl-d:preview-half-page-down,ctrl-u:preview-half-page-up' \
      --preview="target=$target_quoted; $preview_script" --preview-window='right:65%:wrap'
}

action="$direct_action"
if [[ -z "$action" ]]; then
  command -v fzf >/dev/null 2>&1 || fail "fzf is not installed"
  selected_action="$(choose_action)" || exit 0
  case "$selected_action" in
    'open in browser') action="open_browser" ;;
    'open with Finder') action="open_finder" ;;
    'replace in current window nvim') action="replace_window_nvim" ;;
    'copy absolute path') action="copy_absolute" ;;
    'copy relative path') action="copy_relative" ;;
    'new tmux pane + nvim') action="new_pane_nvim" ;;
    'new tmux window + nvim') action="new_window_nvim" ;;
    'copy as Finder file') action="copy_finder" ;;
    *) exit 0 ;;
  esac
fi

case "$action" in
  open_finder)
    if [[ -d "$absolute_path" ]]; then
      open -a Finder "$absolute_path"
    else
      open -R "$absolute_path"
    fi
    ;;
  open_browser)
    open_in_browser
    ;;
  replace_window_nvim)
    open_in_current_window_nvim
    ;;
  copy_absolute)
    copy_text "$absolute_path"
    notify "Copied absolute path: $absolute_path"
    ;;
  copy_relative)
    copy_text "$relative_path"
    notify "Copied relative path: $relative_path"
    ;;
  copy_finder)
    copy_finder_file
    notify "Copied Finder file: $absolute_path"
    ;;
  new_pane_nvim)
    open_in_new_pane
    ;;
  new_window_nvim)
    open_in_new_window
    ;;
  *)
    fail "unknown action: $action"
    ;;
esac
