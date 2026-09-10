#!/bin/bash
# @raycast.schemaVersion 1
# @raycast.title Open Scratchpad
# @raycast.mode silent
# @raycast.icon ✏️
# @raycast.packageName Task Board
# @raycast.description Open today's ~/scratchpad/YYYY-MM-DD.md in Neovide, or focus it if already open (same as ⌘⇧M)

if pgrep -xq Hammerspoon; then
  open -g "hammerspoon://scratchpad"
else
  mkdir -p "$HOME/scratchpad"
  /opt/homebrew/bin/neovide -- "$HOME/scratchpad/$(date +%F).md"
fi
