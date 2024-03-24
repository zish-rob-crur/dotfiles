#! /bin/bash

echo "Do you want to install Docker? (Y/N)"
read answer

if [ "$answer" != "${answer#[Yy]}" ] ;then
    echo "Installing Docker..."
    sudo apt-get update
    sudo apt-get install ca-certificates curl
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    # Add the repository to Apt sources:
    echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update

    sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

else
    echo "Skipping Docker installation..."
fi

echo "Do you want to install neovim? (Y/N)"
read answer

if [ "$answer" != "${answer#[Yy]}" ] ;then
    echo "Installing neovim..."
    sudo apt-get install neovim
else
    echo "Skipping neovim installation..."
fi

echo "Do you want to install tmux? (Y/N)"
read answer

if [ "$answer" != "${answer#[Yy]}" ] ;then
    echo "Installing tmux..."
    sudo apt-get install tmux
else
    echo "Skipping tmux installation..."
fi

# git clone my dotfiles
sudo apt-get install git
#  check my ~/GithubRepos exists
if [ ! -d ~/GithubRepos ]; then
    mkdir ~/GithubRepos
fi
git clone https://github.com/zish-rob-crur/dotfiles.git ~/GithubRepos/dotfiles

# install zsh
sudo apt-get install zsh
ln -s ~/GithubRepos/dotfiles/.zshrc .zshrc
ln -s ~/GithubRepos/dotfiles/.p10k.zsh .p10k.zsh

ln -s ~/GithubRepos/dotfiles/.vimrc .vimrc

git submodule update --init --recursive
mv ~/.config/nvim ~/.config/nvim.bak
mv ~/.local/share/nvim ~/.local/share/nvim.bak
ln -s ~/GithubRepos/dotfiles/nvim ~/.config/nvim

git clone https://github.com/tmux-plugins/tpm ~/.tmux/plugins/tpm
ln -s ~/GithubRepos/dotfiles/tmux/.tmux.conf .tmux.conf