#!/usr/bin/env bash
set -euo pipefail

if command -v pbcopy >/dev/null 2>&1; then
  if command -v reattach-to-user-namespace >/dev/null 2>&1; then
    reattach-to-user-namespace pbcopy
  elif [[ -x /opt/homebrew/bin/reattach-to-user-namespace ]]; then
    /opt/homebrew/bin/reattach-to-user-namespace pbcopy
  elif [[ -x /usr/local/bin/reattach-to-user-namespace ]]; then
    /usr/local/bin/reattach-to-user-namespace pbcopy
  else
    pbcopy
  fi
elif command -v wl-copy >/dev/null 2>&1; then
  wl-copy
elif command -v xclip >/dev/null 2>&1; then
  xclip -in -selection clipboard
elif command -v xsel >/dev/null 2>&1; then
  xsel --clipboard --input
elif command -v clip.exe >/dev/null 2>&1; then
  clip.exe
else
  cat >/dev/null
fi
