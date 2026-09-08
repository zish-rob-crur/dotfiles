#!/bin/bash
# @raycast.schemaVersion 1
# @raycast.title Add Todo
# @raycast.mode silent
# @raycast.icon ✅
# @raycast.packageName Task Board
# @raycast.description Append one item to this week's todo file
# @raycast.argument1 { "type": "text", "placeholder": "todo text (@window #source 📅 date)" }
# @raycast.argument2 { "type": "dropdown", "placeholder": "vault", "optional": true, "data": [{ "title": "work", "value": "work" }, { "title": "personal", "value": "personal" }] }

vault="${2:-work}"
file="$("$HOME/.local/bin/todo-notes" add "$vault" "$1")" || { echo "Failed to add todo"; exit 1; }
echo "Added to $vault: $1"
