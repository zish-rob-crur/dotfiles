#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
base_dir="$PWD"
target_pane="${TMUX_PANE:-}"
requested_path=""
direct_action=""

usage() {
  cat <<'EOF'
Usage: path-actions.sh [options] [path]

Select a file or directory, then copy its path or open it with Neovim.

Options:
  --cwd DIR          Resolve relative paths from DIR
  --pane PANE_ID     Split from this tmux pane
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

choose_path() {
  command -v fzf >/dev/null 2>&1 || fail "fzf is not installed"

  local preview
  preview='target={}
if [ -d "$target" ]; then
  if command -v eza >/dev/null 2>&1; then
    eza --all --color=always --icons=always --group-directories-first "$target"
  elif command -v tree >/dev/null 2>&1; then
    tree -C -L 2 "$target" | head -200
  else
    find "$target" -maxdepth 2 -print | head -200
  fi
elif command -v bat >/dev/null 2>&1; then
  bat --style=numbers --color=always --line-range=:300 -- "$target"
else
  file -- "$target"
  sed -n "1,200p" -- "$target" 2>/dev/null
fi'

  list_paths | FZF_DEFAULT_OPTS="" fzf \
    --layout=reverse --border --height=100% \
    --prompt='Path > ' --pointer='▶' \
    --preview="$preview" --preview-window='right:60%:wrap'
}

if [[ -z "$requested_path" ]]; then
  requested_path="$(choose_path)" || exit 0
fi

[[ -e "$requested_path" ]] || fail "path does not exist: $requested_path"
absolute_path="$(realpath "$requested_path")"

if command -v python3 >/dev/null 2>&1; then
  relative_path="$(python3 - "$absolute_path" "$base_dir" <<'PY'
import os
import sys

print(os.path.relpath(sys.argv[1], sys.argv[2]))
PY
)"
elif [[ "$absolute_path" == "$base_dir" ]]; then
  relative_path="."
elif [[ "$absolute_path" == "$base_dir/"* ]]; then
  relative_path="${absolute_path#"$base_dir/"}"
else
  relative_path="$absolute_path"
fi

copy_text() {
  local value="$1"
  [[ -x "$script_dir/copy-to-clipboard.sh" ]] || fail "clipboard helper is not executable"
  printf '%s' "$value" | "$script_dir/copy-to-clipboard.sh"
}

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

open_in_new_pane() {
  local nvim_bin editor_command
  local -a tmux_args
  require_tmux_nvim
  if [[ -z "$target_pane" ]]; then
    target_pane="$(tmux display-message -p '#{pane_id}' 2>/dev/null || true)"
  fi
  nvim_bin="$(command -v nvim)"
  printf -v editor_command 'exec %q -- %q' "$nvim_bin" "$absolute_path"
  tmux_args=(split-window -h -c "$editor_working_dir")
  [[ -z "$target_pane" ]] || tmux_args+=(-t "$target_pane")
  tmux "${tmux_args[@]}" "$editor_command"
}

open_in_new_window() {
  local nvim_bin editor_command
  require_tmux_nvim
  nvim_bin="$(command -v nvim)"
  printf -v editor_command 'exec %q -- %q' "$nvim_bin" "$absolute_path"
  tmux new-window -c "$editor_working_dir" "$editor_command"
}

choose_action() {
  local header
  header="$(printf 'Relative: %s\nAbsolute: %s' "$relative_path" "$absolute_path")"
  printf '%s\n' \
    'copy absolute path' \
    'copy relative path' \
    'new tmux pane + nvim' \
    'new tmux window + nvim' \
    'copy as Finder file' |
    FZF_DEFAULT_OPTS="" fzf \
      --layout=reverse --border --height=100% \
      --prompt='Action > ' --pointer='▶' --header="$header"
}

action="$direct_action"
if [[ -z "$action" ]]; then
  command -v fzf >/dev/null 2>&1 || fail "fzf is not installed"
  selected_action="$(choose_action)" || exit 0
  case "$selected_action" in
    'copy absolute path') action="copy_absolute" ;;
    'copy relative path') action="copy_relative" ;;
    'new tmux pane + nvim') action="new_pane_nvim" ;;
    'new tmux window + nvim') action="new_window_nvim" ;;
    'copy as Finder file') action="copy_finder" ;;
    *) exit 0 ;;
  esac
fi

case "$action" in
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
