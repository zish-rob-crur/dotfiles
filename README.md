# dotfiles

## oh my zsh

```shell
ln -s ~/GithubRepos/dotfiles/.zshrc .zshrc
ln -s ~/GithubRepos/dotfiles/.p10k.zsh .p10k.zsh
```

## vimrc

```shell
ln -s ~/GithubRepos/dotfiles/.vimrc .vimrc
```

### neoVim
```shell
git submodule update --init --recursive
mv ~/.config/nvim ~/.config/nvim.bak
mv ~/.local/share/nvim ~/.local/share/nvim.bak
ln -s ~/GithubRepos/dotfiles/nvim ~/.config/nvim
```

## Wezterm
```shell
ln -s GitHubRepos/dotfiles/wezterm/.wezterm.lua .wezterm.lua
```

## tmux
```shell
git clone https://github.com/tmux-plugins/tpm ~/.tmux/plugins/tpm
ln -s ~/GithubRepos/dotfiles/tmux/.tmux.conf .tmux.conf
pip install libtmux
tmux 
<Ctrl-b> + I (install plugins)
<Ctrl-b> + r (reload tmux)
```
