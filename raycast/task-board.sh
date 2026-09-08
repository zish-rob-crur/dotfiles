#!/bin/bash
# @raycast.schemaVersion 1
# @raycast.title Toggle Task Board
# @raycast.mode silent
# @raycast.icon 🗂️
# @raycast.packageName Task Board
# @raycast.description Show or hide the tmux task board window (same as ⌘⇧B)

open -g "hammerspoon://task-board?action=toggle" && echo "Task Board toggled"
