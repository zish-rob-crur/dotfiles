#!/usr/bin/env bash
# Where fzf's cursor belongs for the current query in fzf-goto.sh, printed as an
# fzf action. A query of digits alone means a window number ("20" is window 20,
# not the task mentioning 2026), so it wins over the first text match; anything
# else goes to the best match, and an empty query returns to the current window.
set -euo pipefail

query=${1-}
start=${2:-1}
map=${3-}  # lines of "<window index><TAB><line number>"

# Keep the group indicator and next-group hint in sync with loaded results.
view_file="$(dirname "$start")/view"
view=all
[[ ! -f "$view_file" ]] || view=$(cat "$view_file")
case "$view" in
  pinned) label=Pinned; next=Working; tabs=$'All  ·  \033[1;36m[Pinned]\033[0m  ·  Working  ·  Completed' ;;
  working) label=Working; next=Completed; tabs=$'All  ·  Pinned  ·  \033[1;36m[Working]\033[0m  ·  Completed' ;;
  completed) label=Completed; next=All; tabs=$'All  ·  Pinned  ·  Working  ·  \033[1;36m[Completed]\033[0m' ;;
  *) label=All; next=Pinned; tabs=$'\033[1;36m[All]\033[0m  ·  Pinned  ·  Working  ·  Completed' ;;
esac
printf -v header '%s    Ctrl+S → %s\nEnter: go · ↑↓: windows · Tab: panes · Alt+P: pin · Ctrl+/: preview · Ctrl+O: focus only · Ctrl+G: this client' "$tabs" "$next"
if [[ "${4-}" == --header ]]; then
  printf '%s\n' "$header"
  exit 0
fi
printf -v prompt_action 'change-prompt(%s > )+change-header(%s)' "$label" "$header"

# Files, because a pin reload moves the lines and rewrites them.
if [[ -f "$start" ]]; then start=$(cat "$start"); fi
# A pin reload keeps the selection (fzf --track); only query edits reposition it.
if [[ "${FZF_KEY-}" == alt-p ]]; then
  [[ -z "$prompt_action" ]] || printf '%s\n' "$prompt_action"
  exit 0
fi
[[ -z "$prompt_action" ]] || printf '%s+' "$prompt_action"

if [[ -z "$query" ]]; then
  printf 'pos(%s)\n' "$start"
  exit 0
fi

if [[ "$query" =~ ^[0-9]+$ && -r "$map" ]]; then
  line=$(awk -F'\t' -v want="$query" '$1 == want { print $2; exit }' "$map")
  if [[ -n "$line" ]]; then
    printf 'pos(%s)\n' "$line"
    exit 0
  fi
fi

# The external filter already ranks window groups; fzf matching is disabled.
echo 'pos(1)'
