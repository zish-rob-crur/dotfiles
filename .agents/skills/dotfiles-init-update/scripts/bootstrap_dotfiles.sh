#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SKILL_DIR="$(cd -P -- "${SCRIPT_DIR}/.." && pwd -P)"
DEFAULT_DOTFILES_REPO="$(cd -P -- "${SKILL_DIR}/../../.." && pwd -P)"

MODE="update"
DRY_RUN=0
DOTFILES_REPO="${DOTFILES_REPO:-$DEFAULT_DOTFILES_REPO}"
DOTFILES_REMOTE="${DOTFILES_REMOTE:-git@github.com:zish-rob-crur/dotfiles.git}"
ASTRO_REPO="${ASTRO_REPO:-$HOME/GitHubRepos/zish-rob-crur/AstroNvim}"
ASTRO_REMOTE="${ASTRO_REMOTE:-https://github.com/zish-rob-crur/AstroNvim.git}"
TPM_DIR="${TPM_DIR:-$HOME/.tmux/plugins/tpm}"
BACKUP_SUFFIX="$(date +%Y%m%d%H%M%S)"

usage() {
  cat <<'EOF'
Usage:
  bootstrap_dotfiles.sh --mode init|update [--dry-run]
                        [--dotfiles-repo PATH] [--dotfiles-remote URL]
                        [--astro-repo PATH] [--astro-remote URL]
                        [--tpm-dir PATH]
EOF
}

log() {
  printf '[dotfiles-init] %s\n' "$*"
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

ensure_git_repo() {
  local path="$1"
  local remote="$2"
  local label="$3"

  if [ ! -e "$path" ]; then
    run mkdir -p "$(dirname "$path")"
    log "Clone ${label}: ${remote} -> ${path}"
    run git clone "$remote" "$path"
    return 0
  fi

  if git -C "$path" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 0
  fi

  log "Refuse to continue: ${label} path exists but is not a git repo: ${path}"
  return 1
}

backup_path() {
  local path="$1"
  if [ -e "$path" ] || [ -L "$path" ]; then
    local backup="${path}.bak.${BACKUP_SUFFIX}"
    log "Backup ${path} -> ${backup}"
    run mv "$path" "$backup"
  fi
}

link_path() {
  local src="$1"
  local dst="$2"

  if [ ! -e "$src" ]; then
    log "Skip missing source: ${src}"
    return 0
  fi

  run mkdir -p "$(dirname "$dst")"

  if [ -e "$dst" ] || [ -L "$dst" ]; then
    if [ "$dst" -ef "$src" ] 2>/dev/null; then
      log "Already linked: ${dst}"
      return 0
    fi
  fi

  if [ -e "$dst" ] || [ -L "$dst" ]; then
    backup_path "$dst"
  fi

  log "Link ${dst} -> ${src}"
  run ln -s "$src" "$dst"
}

ensure_dotfiles_links() {
  local repo="$1"

  link_path "${repo}/.zshrc" "${HOME}/.zshrc"
  link_path "${repo}/.p10k.zsh" "${HOME}/.p10k.zsh"
  link_path "${repo}/.ideavimrc" "${HOME}/.ideavimrc"
  link_path "${repo}/vim/.vimrc" "${HOME}/.vimrc"
  link_path "${repo}/tmux/.tmux.conf" "${HOME}/.tmux.conf"
  link_path "${repo}/alacritty/alacritty.toml" "${HOME}/.config/alacritty/alacritty.toml"
  link_path "${repo}/btop/themes" "${HOME}/.config/btop/themes"
  if [ "$(uname)" = "Darwin" ]; then
    link_path "${repo}/ghostty/config" "${HOME}/.config/ghostty/config"
    link_path "${repo}/ghostty/shaders/unfocused_mute.glsl" "${HOME}/.config/ghostty/shaders/unfocused_mute.glsl"
    link_path "${repo}/karabiner/karabiner.json" "${HOME}/.config/karabiner/karabiner.json"
  fi
  link_path "${repo}/fzf_scripts/ssh-fzf.sh" "${HOME}/.local/bin/ssh-fzf"
}

ensure_tpm() {
  if [ -d "$TPM_DIR" ]; then
    log "TPM already installed: ${TPM_DIR}"
    return 0
  fi

  run mkdir -p "$(dirname "$TPM_DIR")"
  log "Install TPM into ${TPM_DIR}"
  run git clone https://github.com/tmux-plugins/tpm "$TPM_DIR"
}

ensure_submodules() {
  local repo="$1"
  if [ -f "${repo}/.gitmodules" ]; then
    log "Update submodules in ${repo}"
    run git -C "$repo" submodule update --init --recursive
  fi
}

update_repo_if_clean() {
  local repo="$1"
  local label="$2"

  if [ -n "$(git -C "$repo" status --porcelain 2>/dev/null)" ]; then
    log "Skip update for dirty repo: ${label} (${repo})"
    return 0
  fi

  log "Pull latest for ${label}"
  run git -C "$repo" pull --ff-only
}

ensure_astronvim() {
  ensure_git_repo "$ASTRO_REPO" "$ASTRO_REMOTE" "AstroNvim"
  link_path "$ASTRO_REPO" "${HOME}/.config/nvim"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode)
      MODE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --dotfiles-repo)
      DOTFILES_REPO="$2"
      shift 2
      ;;
    --dotfiles-remote)
      DOTFILES_REMOTE="$2"
      shift 2
      ;;
    --astro-repo)
      ASTRO_REPO="$2"
      shift 2
      ;;
    --astro-remote)
      ASTRO_REMOTE="$2"
      shift 2
      ;;
    --tpm-dir)
      TPM_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "$MODE" in
  init|update)
    ;;
  *)
    printf 'Unsupported mode: %s\n\n' "$MODE" >&2
    usage >&2
    exit 1
    ;;
esac

log "Mode: ${MODE}"
log "Dotfiles repo: ${DOTFILES_REPO}"
log "AstroNvim repo: ${ASTRO_REPO}"

ensure_git_repo "$DOTFILES_REPO" "$DOTFILES_REMOTE" "dotfiles"
if [ "$MODE" = "update" ]; then
  update_repo_if_clean "$DOTFILES_REPO" "dotfiles"
fi
ensure_submodules "$DOTFILES_REPO"
ensure_dotfiles_links "$DOTFILES_REPO"
ensure_tpm
ensure_git_repo "$ASTRO_REPO" "$ASTRO_REMOTE" "AstroNvim"
if [ "$MODE" = "update" ]; then
  update_repo_if_clean "$ASTRO_REPO" "AstroNvim"
fi
ensure_astronvim

log "Done."
