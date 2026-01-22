#!/usr/bin/env bash
set -euo pipefail

if command -v pbcopy >/dev/null 2>&1; then
  pbcopy
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
