# dotfiles

## Bootstrap

```shell
scripts/bootstrap_dotfiles.sh --mode init
```

This links the managed dotfiles, installs TPM, links AstroNvim from
`~/GithubRepos/zish-rob-crur/AstroNvim`, installs the macOS package list from
`Brewfile`, and installs the default Node version through `nvm`.

For package-only refreshes:

```shell
scripts/install_brewfile_parallel.sh Brewfile
```

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
ln -s ~/GithubRepos/zish-rob-crur/AstroNvim ~/.config/nvim
```

## Ghostty

```shell
mkdir -p ~/.config/ghostty
ln -s ~/GithubRepos/dotfiles/ghostty/config ~/.config/ghostty/config
mkdir -p ~/.config/ghostty/shaders
ln -s ~/GithubRepos/dotfiles/ghostty/shaders/unfocused_mute.glsl ~/.config/ghostty/shaders/unfocused_mute.glsl
```

## tmux

## Install Package

### Mac OS

```shell
brew bundle --file Brewfile
```

The bootstrap script uses `scripts/install_brewfile_parallel.sh` for a faster
formula/cask install from the same `Brewfile`.

### Ubuntu

```shell
sudo apt install fzf fd-find ripgrep
```

```shell
mkdir -p ~/.tmux/plugins
git clone https://github.com/tmux-plugins/tpm ~/.tmux/plugins/tpm
ln -s ~/GithubRepos/dotfiles/tmux/.tmux.conf .tmux.conf
tmux
<Ctrl-b> + I (install plugins)
<Ctrl-b> + r (reload tmux)
<Ctrl-b> + Tab (open extrakto)
<Ctrl-b> + e (open treemux sidebar)
```

## Git

- `~/.gitconfig` is linked from `git/.gitconfig`.
- Shared defaults live in `git/.gitconfig.shared`.
- Shared light `delta` theme lives in `git/.gitconfig.delta-light`.
- Machine-specific settings stay in `~/.gitconfig.local`.
- `~/.gitconfig` only includes the shared repo config and the local machine config.
- `git/.gitconfig.local.example` shows the expected local structure, including the optional `delta` include.

Personal machines can use this local identity:

```shell
git config --file ~/.gitconfig.local user.name zish
git config --file ~/.gitconfig.local user.email me@zish-rob-crur.com
```

Do not create `~/.gitconfig.local` on work machines unless a work identity is needed.

## Codex Skills

- Keep personal/project skills in this repo under `.agents/skills/<skill-name>`.

## Init My Dev Linux

```shell
curl -sS https://raw.githubusercontent.com/zish-rob-crur/dotfiles/main/linux/init_dev.sh  -o init_dev.sh
chmod +x init_dev.sh
./init_dev.sh
```

def 
