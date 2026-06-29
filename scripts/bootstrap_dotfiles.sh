#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: scripts/bootstrap_dotfiles.sh --mode init|update [--dry-run] [--skip-brew]

Bootstraps zish dotfiles links, AstroNvim, TPM, and the macOS Brewfile.
USAGE
}

MODE=""
DRY_RUN=0
INSTALL_BREW=1

while [ "$#" -gt 0 ]; do
    case "$1" in
        --mode)
            MODE="${2:-}"
            shift 2
            ;;
        --mode=*)
            MODE="${1#*=}"
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --skip-brew)
            INSTALL_BREW=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ "${MODE}" != "init" ] && [ "${MODE}" != "update" ]; then
    echo "--mode must be init or update" >&2
    usage >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DOTFILES_REPO="${DOTFILES_REPO:-$(cd "${SCRIPT_DIR}/.." && pwd -P)}"
DOTFILES_REMOTE="${DOTFILES_REMOTE:-git@github.com:zish-rob-crur/dotfiles.git}"
ASTRO_REPO="${ASTRO_REPO:-$(dirname "${DOTFILES_REPO}")/zish-rob-crur/AstroNvim}"
ASTRO_REMOTE="${ASTRO_REMOTE:-https://github.com/zish-rob-crur/AstroNvim.git}"
TPM_DIR="${TPM_DIR:-${HOME}/.tmux/plugins/tpm}"
BACKUP_SUFFIX="$(date +%Y%m%d%H%M%S)"

run() {
    if [ "${DRY_RUN}" -eq 1 ]; then
        printf 'DRY-RUN:'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

ensure_git_repo() {
    local repo="$1"
    local remote="$2"
    local label="$3"

    if [ -d "${repo}" ] && [ ! -d "${repo}/.git" ]; then
        echo "Refusing to use non-git ${label} path: ${repo}" >&2
        exit 1
    fi

    if [ ! -d "${repo}" ]; then
        if [ "${MODE}" = "init" ]; then
            run mkdir -p "$(dirname "${repo}")"
            run git clone "${remote}" "${repo}"
        else
            echo "Skip missing ${label} repo in update mode: ${repo}"
        fi
        return 0
    fi

    if [ "${MODE}" = "update" ]; then
        if [ -n "$(git -C "${repo}" status --porcelain)" ]; then
            echo "Skip dirty ${label} repo: ${repo}"
        else
            run git -C "${repo}" pull --ff-only
        fi
    fi
}

link_path() {
    local src="$1"
    local dst="$2"

    if [ ! -e "${src}" ]; then
        echo "Skip missing source: ${src}"
        return 0
    fi

    run mkdir -p "$(dirname "${dst}")"

    if [ -L "${dst}" ] && [ "$(readlink "${dst}")" = "${src}" ]; then
        echo "Already linked: ${dst}"
        return 0
    fi

    if [ -e "${dst}" ] || [ -L "${dst}" ]; then
        run mv "${dst}" "${dst}.bak.${BACKUP_SUFFIX}"
    fi

    run ln -s "${src}" "${dst}"
}

ensure_executable() {
    local path="$1"

    if [ ! -e "${path}" ]; then
        echo "Skip missing executable: ${path}"
        return 0
    fi

    if [ -x "${path}" ]; then
        echo "Executable already set: ${path}"
        return 0
    fi

    run chmod +x "${path}"
}

install_font() {
    local src="$1"
    local dst="$2"

    if [ ! -e "${src}" ]; then
        echo "Skip missing font: ${src}"
        return 0
    fi

    run mkdir -p "$(dirname "${dst}")"

    if [ -f "${dst}" ] && [ ! -L "${dst}" ] && cmp -s "${src}" "${dst}"; then
        echo "Already installed font: ${dst}"
        return 0
    fi

    if [ -e "${dst}" ] || [ -L "${dst}" ]; then
        run mv "${dst}" "${dst}.bak.${BACKUP_SUFFIX}"
    fi

    run cp "${src}" "${dst}"
}

ensure_tpm() {
    if [ -d "${TPM_DIR}" ]; then
        echo "TPM already installed: ${TPM_DIR}"
        return 0
    fi

    run mkdir -p "$(dirname "${TPM_DIR}")"
    run git clone https://github.com/tmux-plugins/tpm "${TPM_DIR}"
}

ensure_nvm_node() {
    if [ "$(uname -s)" != "Darwin" ] || [ "${INSTALL_BREW}" -eq 0 ]; then
        return 0
    fi

    local nvm_sh="/opt/homebrew/opt/nvm/nvm.sh"
    local default_node="${NVM_DEFAULT_NODE:-node}"

    run mkdir -p "${HOME}/.nvm"

    if [ "${DRY_RUN}" -eq 1 ]; then
        echo "DRY-RUN: source ${nvm_sh} && nvm install ${default_node} && nvm alias default ${default_node}"
        return 0
    fi

    if [ ! -s "${nvm_sh}" ]; then
        echo "nvm is not available at ${nvm_sh}" >&2
        exit 1
    fi

    # shellcheck disable=SC1090
    . "${nvm_sh}"
    nvm install "${default_node}"
    nvm alias default "${default_node}"
    nvm use default
}

ensure_brew() {
    if [ "$(uname -s)" != "Darwin" ] || [ "${INSTALL_BREW}" -eq 0 ]; then
        return 0
    fi

    if [ -x /opt/homebrew/bin/brew ]; then
        BREW=/opt/homebrew/bin/brew
    elif [ -x /usr/local/bin/brew ]; then
        BREW=/usr/local/bin/brew
    elif command -v brew >/dev/null 2>&1; then
        BREW="$(command -v brew)"
    else
        if [ "${DRY_RUN}" -eq 1 ]; then
            echo "DRY-RUN: install Homebrew"
        else
            NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi
        BREW=/opt/homebrew/bin/brew
    fi

    if [ -x "${BREW}" ]; then
        run "${DOTFILES_REPO}/scripts/install_brewfile_parallel.sh" "${DOTFILES_REPO}/Brewfile"
        ensure_nvm_node
    else
        echo "Homebrew is not available after install attempt" >&2
        exit 1
    fi
}

ensure_git_repo "${DOTFILES_REPO}" "${DOTFILES_REMOTE}" "dotfiles"
ensure_git_repo "${ASTRO_REPO}" "${ASTRO_REMOTE}" "AstroNvim"

run git -C "${DOTFILES_REPO}" submodule update --init --recursive

link_path "${DOTFILES_REPO}/.zshrc" "${HOME}/.zshrc"
link_path "${DOTFILES_REPO}/.p10k.zsh" "${HOME}/.p10k.zsh"
link_path "${DOTFILES_REPO}/.ideavimrc" "${HOME}/.ideavimrc"
link_path "${DOTFILES_REPO}/vim/.vimrc" "${HOME}/.vimrc"
link_path "${DOTFILES_REPO}/git/.gitconfig" "${HOME}/.gitconfig"
link_path "${DOTFILES_REPO}/tmux/.tmux.conf" "${HOME}/.tmux.conf"
link_path "${DOTFILES_REPO}/alacritty/alacritty.toml" "${HOME}/.config/alacritty/alacritty.toml"
link_path "${DOTFILES_REPO}/btop/themes" "${HOME}/.config/btop/themes"
link_path "${DOTFILES_REPO}/bin/nvim-agent" "${HOME}/.local/bin/nvim-agent"
link_path "${DOTFILES_REPO}/fzf_scripts/ssh-fzf.sh" "${HOME}/.local/bin/ssh-fzf"
ensure_executable "${DOTFILES_REPO}/tmux/assistant-launcher.sh"
ensure_executable "${DOTFILES_REPO}/tmux/restart-assistant-panes.py"

if [ "$(uname -s)" = "Darwin" ]; then
    link_path "${DOTFILES_REPO}/ghostty/config" "${HOME}/.config/ghostty/config"
    link_path "${DOTFILES_REPO}/ghostty/shaders/unfocused_mute.glsl" "${HOME}/.config/ghostty/shaders/unfocused_mute.glsl"
    link_path "${DOTFILES_REPO}/karabiner/karabiner.json" "${HOME}/.config/karabiner/karabiner.json"
    install_font "${DOTFILES_REPO}/fonts/CodexStatusSymbols.ttf" "${HOME}/Library/Fonts/CodexStatusSymbols.ttf"
fi

if [ -d "${ASTRO_REPO}" ]; then
    link_path "${ASTRO_REPO}" "${HOME}/.config/nvim"
fi

ensure_tpm
ensure_brew
