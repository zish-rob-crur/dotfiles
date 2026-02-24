#! /bin/bash

# Resolve dotfiles directory (repo root)
DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BACKUP_SUFFIX="$(date +%Y%m%d%H%M%S)"

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

# check if Homebrew is installed
if ! command -v brew >/dev/null 2>&1; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# 必备的一些命令行工具 tmux, neovim, zsh, fzf, ripgrep, fd, bat, exa
brew install tmux neovim zsh fzf ripgrep fd bat exa

# Symlinks (keep configs in sync with dotfiles)
link_path "${DOTFILES_DIR}/.zshrc" "${HOME}/.zshrc"
link_path "${DOTFILES_DIR}/.p10k.zsh" "${HOME}/.p10k.zsh"
link_path "${DOTFILES_DIR}/.ideavimrc" "${HOME}/.ideavimrc"
link_path "${DOTFILES_DIR}/vim/.vimrc" "${HOME}/.vimrc"
link_path "${DOTFILES_DIR}/tmux/.tmux.conf" "${HOME}/.tmux.conf"
link_path "${DOTFILES_DIR}/wezterm/.wezterm.lua" "${HOME}/.wezterm.lua"

# XDG configs
link_path "${DOTFILES_DIR}/alacritty/alacritty.toml" "${HOME}/.config/alacritty/alacritty.toml"
link_path "${DOTFILES_DIR}/btop/themes" "${HOME}/.config/btop/themes"
link_path "${DOTFILES_DIR}/karabiner/karabiner.json" "${HOME}/.config/karabiner/karabiner.json"

# Helper scripts
link_path "${DOTFILES_DIR}/fzf_scripts/ssh-fzf.sh" "${HOME}/.local/bin/ssh-fzf"
