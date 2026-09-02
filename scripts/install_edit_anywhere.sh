#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DOTFILES_REPO="${DOTFILES_REPO:-$(cd -P -- "${SCRIPT_DIR}/.." && pwd -P)}"
DRY_RUN=0
BACKUP_SUFFIX="$(date +%Y%m%d%H%M%S)"
CACHE_DIR="${HOME}/.cache/edit-anywhere"

usage() {
  cat <<'EOF'
Usage: install_edit_anywhere.sh [--dry-run] [--dotfiles-repo PATH]

Install the dotfiles-owned Edit Anywhere runtime without modifying ~/.config/nvim.
EOF
}

log() {
  printf '[edit-anywhere-install] %s\n' "$*"
}

run() {
  if (( DRY_RUN )); then
    printf '[dry-run] '
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

while (( $# )); do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --dotfiles-repo)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      DOTFILES_REPO="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "${DOTFILES_REPO}/.git" && ! -f "${DOTFILES_REPO}/.git" ]]; then
  printf 'Not a dotfiles repository: %s\n' "${DOTFILES_REPO}" >&2
  exit 1
fi
DOTFILES_REPO="$(cd -P -- "${DOTFILES_REPO}" && pwd -P)"

require_source() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    printf 'Missing required Edit Anywhere source: %s\n' "$path" >&2
    exit 1
  fi
}

backup_path() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    local backup="${path}.bak.${BACKUP_SUFFIX}"
    log "Backup ${path} -> ${backup}"
    run mv "$path" "$backup"
  fi
}

link_path() {
  local source="$1"
  local target="$2"
  require_source "$source"
  run mkdir -p "$(dirname -- "$target")"
  if [[ -L "$target" && "$(readlink "$target")" == "$source" ]]; then
    log "Already linked: ${target}"
    return 0
  fi
  backup_path "$target"
  log "Link ${target} -> ${source}"
  run ln -s "$source" "$target"
}

ensure_mode() {
  local mode="$1"
  local path="$2"
  if (( DRY_RUN )); then
    run chmod "$mode" "$path"
  elif [[ "$(stat -f '%Lp' "$path" 2>/dev/null || stat -c '%a' "$path")" != "$mode" ]]; then
    run chmod "$mode" "$path"
  fi
}

check_nvim() {
  local nvim_bin version major minor
  nvim_bin="$(command -v nvim || true)"
  if [[ -z "$nvim_bin" ]]; then
    printf 'Neovim is required but was not found on PATH.\n' >&2
    exit 1
  fi
  version="$($nvim_bin --version | sed -n '1s/^NVIM v\([0-9][0-9.]*\).*$/\1/p')"
  major="${version%%.*}"
  minor="${version#*.}"; minor="${minor%%.*}"
  if [[ -z "$version" || "$major" -lt 1 && "$minor" -lt 12 ]]; then
    printf 'Neovim 0.12+ is required; found %s.\n' "${version:-unknown}" >&2
    exit 1
  fi
  log "Neovim ${version}: ${nvim_bin}"
}

compile_ocr() {
  local source="${DOTFILES_REPO}/bin/edit-anywhere-ocr"
  local target="${CACHE_DIR}/edit-anywhere-ocr-bin"
  [[ "$(uname -s)" == "Darwin" ]] || return 0
  require_source "$source"
  if [[ ! -x /usr/bin/swiftc ]]; then
    printf 'Swift compiler is required for the multilingual OCR helper.\n' >&2
    exit 1
  fi
  if [[ -x "$target" && "$target" -nt "$source" ]]; then
    log "OCR helper is current: ${target}"
    return 0
  fi
  log "Compile multilingual OCR helper: ${target}"
  run /bin/bash -c 'exec /usr/bin/swiftc -O -o "$2" - < "$1"' _ "$source" "$target"
  (( DRY_RUN )) || ensure_mode 700 "$target"
}

check_runtime() {
  require_source "${DOTFILES_REPO}/edit-anywhere/nvim/lua/edit_anywhere/bootstrap.lua"
  require_source "${DOTFILES_REPO}/hammerspoon/init.lua"
  require_source "${DOTFILES_REPO}/hammerspoon/edit_anywhere.lua"
  grep -Fq 'require("edit_anywhere")' "${DOTFILES_REPO}/hammerspoon/init.lua" || {
    printf 'Hammerspoon init does not load edit_anywhere.lua.\n' >&2
    exit 1
  }
  grep -Fq 'global:ctrl+backquote=toggle_quick_terminal' "${DOTFILES_REPO}/ghostty/config" || {
    printf 'Ghostty config is missing the Edit Anywhere Quick Terminal key binding.\n' >&2
    exit 1
  }
  grep -Eq '^quick-terminal-animation-duration[[:space:]]*=[[:space:]]*0$' "${DOTFILES_REPO}/ghostty/config" || {
    printf 'Ghostty Quick Terminal animation must be disabled for Edit Anywhere.\n' >&2
    exit 1
  }
  grep -Fq 'edit-anywhere-quick-terminal' "${DOTFILES_REPO}/.zshrc" || {
    printf 'The Quick Terminal dispatcher hook is missing from .zshrc.\n' >&2
    exit 1
  }
}

check_host_apps() {
  [[ "$(uname -s)" == "Darwin" ]] || return 0
  [[ -d /Applications/Hammerspoon.app ]] || log "Warning: Hammerspoon.app was not found in /Applications"
  [[ -d /Applications/Ghostty.app ]] || log "Warning: Ghostty.app was not found in /Applications"
}

check_nvim
check_runtime
check_host_apps

for dir in \
  "$CACHE_DIR" "$CACHE_DIR/sessions" "$CACHE_DIR/server" \
  "$CACHE_DIR/nvim-state" "$CACHE_DIR/nvim-cache" "$CACHE_DIR/work" \
  "$CACHE_DIR/tmp" "$CACHE_DIR/benchmarks"; do
  run mkdir -p "$dir"
  (( DRY_RUN )) || ensure_mode 700 "$dir"
done

link_path "${DOTFILES_REPO}/edit-anywhere/nvim" "${HOME}/.local/share/edit-anywhere/nvim"
link_path "${DOTFILES_REPO}/hammerspoon/init.lua" "${HOME}/.hammerspoon/init.lua"
link_path "${DOTFILES_REPO}/hammerspoon/edit_anywhere.lua" "${HOME}/.hammerspoon/edit_anywhere.lua"

found_bin=0
for source in "${DOTFILES_REPO}"/bin/edit-anywhere-*; do
  [[ -e "$source" ]] || continue
  found_bin=1
  ensure_mode 755 "$source"
  link_path "$source" "${HOME}/.local/bin/$(basename -- "$source")"
done
(( found_bin )) || { printf 'No edit-anywhere binaries found.\n' >&2; exit 1; }
[[ ! -L "${HOME}/.local/bin/edit-anywhere-spawn" ]] || run rm "${HOME}/.local/bin/edit-anywhere-spawn"

compile_ocr

log "Installed from ${DOTFILES_REPO}"
if (( DRY_RUN )); then
  log "Dry-run complete; no files were changed."
else
  log "Reload Hammerspoon and restart the dedicated Ghostty Quick Terminal dispatcher to activate this protocol version."
fi
