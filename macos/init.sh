#! /bin/bash

set -euo pipefail

# Resolve dotfiles directory (repo root)
DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BACKUP_SUFFIX="$(date +%Y%m%d%H%M%S)"
TPM_DIR="${HOME}/.tmux/plugins/tpm"

link_path() {
    local src="$1"
    local dst="$2"

    if [ ! -e "${src}" ]; then
        echo "Skip (not found): ${src}"
        return 0
    fi

    mkdir -p "$(dirname "${dst}")"

    if [ -L "${dst}" ] && [ "$(readlink "${dst}")" = "${src}" ]; then
        echo "Already linked: ${dst}"
        return 0
    fi

    if [ -e "${dst}" ] || [ -L "${dst}" ]; then
        local backup="${dst}.bak.${BACKUP_SUFFIX}"
        mv "${dst}" "${backup}"
        echo "Backed up: ${backup}"
    fi

    ln -s "${src}" "${dst}"
    echo "Linked: ${dst} -> ${src}"
}

install_font() {
    local src="$1"
    local dst="$2"

    if [ ! -e "${src}" ]; then
        echo "Skip font (not found): ${src}"
        return 0
    fi

    mkdir -p "$(dirname "${dst}")"

    if [ -f "${dst}" ] && [ ! -L "${dst}" ] && cmp -s "${src}" "${dst}"; then
        echo "Font already installed: ${dst}"
        return 0
    fi

    if [ -e "${dst}" ] || [ -L "${dst}" ]; then
        local backup="${dst}.bak.${BACKUP_SUFFIX}"
        mv "${dst}" "${backup}"
        echo "Backed up font: ${backup}"
    fi

    cp "${src}" "${dst}"
    echo "Installed font: ${dst}"
}

install_brew_packages() {
    local packages=(tmux neovim zsh fzf ripgrep fd bat eza rust)

    echo "Installing Homebrew packages: ${packages[*]}"
    brew install "${packages[@]}"
}

install_cargo_tools() {
    "${DOTFILES_DIR}/scripts/install_cargo_tools.sh"
}

install_tpm() {
    if [ -d "${TPM_DIR}" ]; then
        echo "TPM already installed: ${TPM_DIR}"
        return 0
    fi

    mkdir -p "$(dirname "${TPM_DIR}")"
    git clone https://github.com/tmux-plugins/tpm "${TPM_DIR}"
    echo "Installed TPM: ${TPM_DIR}"
}

# check if Homebrew is installed
if ! command -v brew >/dev/null 2>&1; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -x /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
fi

if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew installed but brew is not available on PATH" >&2
    exit 1
fi

install_brew_packages
install_cargo_tools

git -C "${DOTFILES_DIR}" submodule update --init --recursive

# Symlinks (keep configs in sync with dotfiles)
link_path "${DOTFILES_DIR}/.zshrc" "${HOME}/.zshrc"
link_path "${DOTFILES_DIR}/.p10k.zsh" "${HOME}/.p10k.zsh"
link_path "${DOTFILES_DIR}/.ideavimrc" "${HOME}/.ideavimrc"
link_path "${DOTFILES_DIR}/vim/.vimrc" "${HOME}/.vimrc"
link_path "${DOTFILES_DIR}/git/.gitconfig" "${HOME}/.gitconfig"
link_path "${DOTFILES_DIR}/tmux/.tmux.conf" "${HOME}/.tmux.conf"

# XDG configs
link_path "${DOTFILES_DIR}/alacritty/alacritty.toml" "${HOME}/.config/alacritty/alacritty.toml"
link_path "${DOTFILES_DIR}/btop/themes" "${HOME}/.config/btop/themes"
link_path "${DOTFILES_DIR}/ghostty/config" "${HOME}/.config/ghostty/config"
link_path "${DOTFILES_DIR}/ghostty/shaders/unfocused_mute.glsl" "${HOME}/.config/ghostty/shaders/unfocused_mute.glsl"
link_path "${DOTFILES_DIR}/karabiner/karabiner.json" "${HOME}/.config/karabiner/karabiner.json"
install_font "${DOTFILES_DIR}/fonts/CodexStatusSymbols.ttf" "${HOME}/Library/Fonts/CodexStatusSymbols.ttf"

# Helper scripts
link_path "${DOTFILES_DIR}/fzf_scripts/ssh-fzf.sh" "${HOME}/.local/bin/ssh-fzf"

install_tpm
