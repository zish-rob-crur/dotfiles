#! /bin/bash

# check if Homebrew is installed
if test ! $(which brew); then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# 必备的一些命令行工具 tmux, neovim, zsh, fzf, ripgrep, fd, bat, exa
brew install tmux neovim zsh fzf ripgrep fd bat exa
