#!/usr/bin/env bash

# window-title.sh
# Print an icon + short title for a tmux window based on the active pane's command
# Usage: window-title.sh <pane_current_command> <pane_current_path>

set -euo pipefail

cmd_raw=${1:-}
path_raw=${2:-}

# Normalize command to lowercase without path
cmd=$(basename -- "${cmd_raw}")
# To support macOS's older bash (3.2), avoid ${var,,}
cmd=$(printf '%s' "$cmd" | tr '[:upper:]' '[:lower:]')

# Resolve a short directory name; show ~ when at $HOME
if [[ -z "${path_raw}" || "${path_raw}" == "$HOME" ]]; then
  base="~"
else
  base=$(basename -- "${path_raw}")
fi

# Default icon: terminal
icon=""

case "$cmd" in
  # Editors
  nvim|vim|vi) icon="" ;;

  # Shells / multiplexers
  zsh|bash|fish|tmux) icon="" ;;

  # Remote
  ssh|mosh|slogin) icon="󰣀" ;;

  # Python ecosystem
  python|python3|ipython|poetry|pipenv|pdm) icon="" ;;

  # JavaScript / TypeScript runtimes & toolchains
  node|nodejs|npm|yarn|pnpm|bun|tsx|ts-node|vite|next|nuxt) icon="" ;;

  # Deno
  deno) icon="" ;;

  # Go
  go|dlv) icon="" ;;

  # Rust
  cargo|rustc|rustup|rust-analyzer) icon="" ;;

  # Ruby
  ruby|irb|rake|bundle|bundler) icon="" ;;

  # Lua
  lua|luajit) icon="" ;;

  # Containers & orchestration
  docker|docker-compose|podman) icon="" ;;
  kubectl|k9s|helm|minikube|kind) icon="☸" ;;

  # Git / TUI tools
  git|lazygit|tig) icon="" ;;

  # System monitors
  htop|btop|top|glances) icon="" ;;

  # Databases / CLIs
  psql|pgcli|mysql|mariadb|mongosh|redis-cli|sqlite3) icon="" ;;

  # Java / build tools
  java|jshell|mvn|mvnw|gradle|gradlew) icon="" ;;

  # C/C++ toolchains
  gcc|g++|clang|clang++|make|cmake|ninja) icon="" ;;
esac

printf "%s %s" "$icon" "$base"
