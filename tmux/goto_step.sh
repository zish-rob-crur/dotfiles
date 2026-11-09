#!/usr/bin/env bash
# Arrow and Tab moves for fzf-goto.sh, printed as an fzf action.
#   goto_step.sh window up|down QUERY N MAP   up/down: window to window while the
#                                             query is empty, match to match otherwise
#   goto_step.sh pane next|prev QUERY N MAP   tab/shift-tab: cycle through the
#                                             current window's line and its panes
# N is fzf's {n} (zero-based; raw mode lists every item in input order, so the
# line is N+1). MAP holds "<window index><TAB><line>" for each window line.
set -euo pipefail

kind=$1 dir=$2 query=$3 line=$(($4 + 1)) map=$5

if [[ $kind == window && -n $query ]]; then
  echo "$dir-match"
  exit 0
fi

# The window lines around the cursor: the one it belongs to and the next one.
read -r prev own next < <(awk -F'\t' -v line="$line" '
  $2 < line { prev = $2 } $2 <= line { own = $2 } $2 > line && !nxt { nxt = $2 }
  END { print (prev ? prev : 0), (own ? own : 1), (nxt ? nxt : 0) }' "$map")

if [[ $kind == window ]]; then
  if [[ $dir == down ]]; then target=$next; else target=$prev; fi
else
  last=$(( next ? next - 1 : ${FZF_TOTAL_COUNT:-$line} ))
  if [[ $dir == next ]]; then
    target=$(( line < last ? line + 1 : own ))
  else
    target=$(( line > own ? line - 1 : last ))
  fi
fi
(( target > 0 )) && echo "pos($target)"
exit 0
