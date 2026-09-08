#!/bin/bash
# @raycast.schemaVersion 1
# @raycast.title Open Weekly Todo
# @raycast.mode silent
# @raycast.icon 📝
# @raycast.packageName Task Board
# @raycast.description Open this week's todo files in Neovide (or focus the one already open)

# Hammerspoon places and de-duplicates the window (same as ⌘⇧D); fall back to the CLI without it.
if pgrep -xq Hammerspoon; then
  open -g "hammerspoon://task-board?action=edit"
else
  "$HOME/.local/bin/todo-notes" edit
fi
echo "Opening this week's todo"
