#!/usr/bin/env bash
set -euo pipefail

title="${1:-tmux}"
message="${2:-}"
subtitle="${3:-}"

if [ -z "$message" ]; then
  exit 0
fi

if command -v terminal-notifier >/dev/null 2>&1; then
  args=(-title "$title" -message "$message" -group "tmux")
  if [ -n "$subtitle" ]; then
    args+=(-subtitle "$subtitle")
  fi
  terminal-notifier "${args[@]}" >/dev/null 2>&1 || true
  exit 0
fi

escape_applescript_string() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

if command -v osascript >/dev/null 2>&1; then
  title="$(escape_applescript_string "$title")"
  message="$(escape_applescript_string "$message")"
  subtitle="$(escape_applescript_string "$subtitle")"

  if [ -n "$subtitle" ]; then
    osascript -e "display notification \"${message}\" with title \"${title}\" subtitle \"${subtitle}\"" >/dev/null 2>&1 || true
  else
    osascript -e "display notification \"${message}\" with title \"${title}\"" >/dev/null 2>&1 || true
  fi
  exit 0
fi

if command -v notify-send >/dev/null 2>&1; then
  notify-send "$title" "$message" >/dev/null 2>&1 || true
  exit 0
fi

exit 0
