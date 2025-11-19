#!/usr/bin/env bash

# window-title.sh
"${TRACE:-false}" && set -x

# Print an icon + short title for a tmux window based on the active pane's command
# Heuristics: project-type (files in repo root) > current command
# Also appends concise Git info when in a repo: " branch[*]"
# Usage: window-title.sh <pane_current_command> <pane_current_path>

set -euo pipefail

cmd_raw=${1:-}
path_raw=${2:-}

# Normalize command to lowercase without path
cmd=$(basename -- "${cmd_raw}")
# To support macOS's older bash (3.2), avoid ${var,,}
cmd=$(printf '%s' "$cmd" | tr '[:upper:]' '[:lower:]')

# Resolve a short directory name; show ~ when at $HOME
is_home="false"
if [[ -z "${path_raw}" || "${path_raw}" == "$HOME" ]]; then
  base="~"
  is_home="true"
else
  base=$(basename -- "${path_raw}")
fi

# Default icon: terminal
icon=""

# Resolve git root if any (suppress errors)
git_root=""
if command -v git >/dev/null 2>&1; then
  git_root=$(git -C "${path_raw:-.}" rev-parse --show-toplevel 2>/dev/null || true)
fi

root_dir=${git_root:-$path_raw}

# Helper: check if any of the candidate files exists in root_dir
has_any() {
  for f in "$@"; do
    if [[ -e "$root_dir/$f" ]]; then
      return 0
    fi
  done
  return 1
}

# Project type detection (cold-colour friendly icons)
if has_any pyproject.toml requirements.txt requirements.in Pipfile poetry.lock setup.py manage.py; then
  icon="" # Python
elif has_any package.json bun.lockb pnpm-lock.yaml yarn.lock tsconfig.json; then
  icon="" # Node / TS
elif has_any go.mod; then
  icon="" # Go
elif has_any Cargo.toml; then
  icon="" # Rust
elif has_any pom.xml build.gradle build.gradle.kts gradlew; then
  icon="" # Java
elif has_any Gemfile; then
  icon="" # Ruby
elif has_any Dockerfile docker-compose.yml docker-compose.yaml compose.yml compose.yaml; then
  icon="" # Docker
elif has_any deno.json deno.jsonc; then
  icon="" # Deno
elif has_any Chart.yaml values.yaml helmfile.yaml; then
  icon="☸" # K8s (Helm)
elif has_any main.tf terraform.tfstate; then
  icon="󱁢" # Terraform (nf-md-terraform, may fallback)
fi

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


# If at the home directory, prefer a Home icon regardless of command/project
if [[ "$is_home" == "true" ]]; then
  icon=""
fi


# Append minimal Git dirty flag only (no branch name)
git_frag=""
if [[ -n "$git_root" ]]; then
  if [[ -n $(git -C "$root_dir" status -uno --porcelain 2>/dev/null | head -n1) ]]; then
    git_frag=" *"
  fi
fi

printf "%s %s%s" "$icon" "$base" "$git_frag"
