# Environment

## Canonical repos

- Dotfiles repo: `~/GitHubRepos/dotfiles`
- Dotfiles remote: `git@github.com:zish-rob-crur/dotfiles.git`
- AstroNvim repo: `~/GitHubRepos/zish-rob-crur/AstroNvim`
- AstroNvim remote: `https://github.com/zish-rob-crur/AstroNvim.git`
- TPM dir: `~/.tmux/plugins/tpm`
- Skill source of truth: `dotfiles/.agents/skills/dotfiles-init-update`
- Codex install path: `~/.codex/skills/dotfiles-init-update` should symlink to the repo copy when this repo is present locally

## Managed links

- `~/.zshrc` -> `dotfiles/.zshrc`
- `~/.p10k.zsh` -> `dotfiles/.p10k.zsh`
- `~/.ideavimrc` -> `dotfiles/.ideavimrc`
- `~/.vimrc` -> `dotfiles/vim/.vimrc`
- `~/.tmux.conf` -> `dotfiles/tmux/.tmux.conf`
- `~/.config/alacritty/alacritty.toml` -> `dotfiles/alacritty/alacritty.toml`
- `~/.config/btop/themes` -> `dotfiles/btop/themes`
- `~/.config/ghostty/config` -> `dotfiles/ghostty/config` on macOS
- `~/.config/ghostty/shaders/unfocused_mute.glsl` -> `dotfiles/ghostty/shaders/unfocused_mute.glsl` on macOS
- `~/.config/karabiner/karabiner.json` -> `dotfiles/karabiner/karabiner.json` on macOS only
- `~/.local/bin/ssh-fzf` -> `dotfiles/fzf_scripts/ssh-fzf.sh`
- `~/.config/nvim` -> `AstroNvim repo`

## Mode guidance

- `init`: Clone missing repos, update dotfiles submodules, install TPM if missing, and create/repair the managed symlinks.
- `update`: Reuse the same link logic, but first run `git pull --ff-only` on clean repos. Dirty repos are reported and skipped.

## Validation

- `git -C <repo> status -sb`
- `readlink ~/.config/nvim`
- `readlink ~/.tmux.conf`
- `readlink ~/.zshrc`
- `readlink ~/.config/ghostty/config` on macOS
- `test -d ~/.tmux/plugins/tpm`

## Existing repo notes

- `README.md` and `linux/init_dev.sh` in the dotfiles repo still show the legacy `dotfiles/nvim` link target.
- `macos/init.sh` already has a safe `link_path` helper and is a good reference for backup semantics, but this skill should keep AstroNvim as the live Neovim target.
