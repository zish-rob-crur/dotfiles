#!/usr/bin/env bash
# Where fzf's cursor belongs for the current query in fzf-goto.sh, printed as an
# fzf action. A query of digits alone means a window number ("20" is window 20,
# not the task mentioning 2026), so it wins over the first text match; anything
# else goes to the best match, and an empty query returns to the current window.
set -euo pipefail

query=${1-}
start=${2:-1}
map=${3-}  # lines of "<window index><TAB><line number>"

# Files, because a pin reload moves the lines and rewrites them.
if [[ -f "$start" ]]; then start=$(cat "$start"); fi
# A pin reload keeps the selection (fzf --track); only query edits reposition it.
[[ "${FZF_KEY-}" == alt-p ]] && exit 0

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
