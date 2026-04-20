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
brew install fzf fd-find ripgrep
```

### Ubuntu

```shell
sudo apt install fzf fd-find ripgrep
```

```shell
git clone https://github.com/tmux-plugins/tpm ~/.tmux/plugins/tpm
ln -s ~/GithubRepos/dotfiles/tmux/.tmux.conf .tmux.conf
tmux
<Ctrl-b> + I (install plugins)
<Ctrl-b> + r (reload tmux)
<Ctrl-b> + Tab (open extrakto)
<Ctrl-b> + e (open treemux sidebar)
```

## Git

- Shared defaults live in `git/.gitconfig.shared`.
- Shared light `delta` theme lives in `git/.gitconfig.delta-light`.
- Machine-specific settings stay in `~/.gitconfig.local`.
- `~/.gitconfig` only includes the shared repo config and the local machine config.
- `git/.gitconfig.local.example` shows the expected local structure, including the optional `delta` include.

## Codex Skills

- `.agents/skills/dotfiles-init-update` is the source of truth for personal dotfiles bootstrap and update.
- Keep `~/.codex/skills/dotfiles-init-update` as a symlink to the repo copy so Codex can auto-discover the skill.
- Bootstrap that link with:

```shell
mkdir -p ~/.codex/skills
ln -s ~/GitHubRepos/dotfiles/.agents/skills/dotfiles-init-update ~/.codex/skills/dotfiles-init-update
```

## Init My Dev Linux

```shell
curl -sS https://raw.githubusercontent.com/zish-rob-crur/dotfiles/main/linux/init_dev.sh  -o init_dev.sh
chmod +x init_dev.sh
./init_dev.sh
```

def 
