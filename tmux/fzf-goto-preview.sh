#!/usr/bin/env bash
set -euo pipefail

session="${1:-}"
line="${2:-}"

if [[ -z "$session" || -z "$line" ]]; then
  exit 0
fi

type="${line%%$'\t'*}"
rest="${line#*$'\t'}"
id="${rest%%$'\t'*}"

if [[ "$type" == "W" ]]; then
  win="$id"
  tmux list-panes -t "${session}:${win}" -F '#{pane_index}: #{pane_title}  #{pane_current_command}  #{pane_current_path}  #{?pane_active,[active],}' 2>/dev/null || true
  exit 0
fi

if [[ "$type" == "P" ]]; then
  pane_id="$id"
  tmux capture-pane -ep -t "${session}:${pane_id}" -S -80 2>/dev/null || true
  exit 0
fi

exit 0
