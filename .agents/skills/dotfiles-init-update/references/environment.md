# Environment

## Canonical repos

- Dotfiles repo: `~/GitHubRepos/dotfiles`
- Dotfiles remote: `git@github.com:zish-rob-crur/dotfiles.git`
- AstroNvim repo: `~/GitHubRepos/zish-rob-crur/AstroNvim`
- AstroNvim remote: `https://github.com/zish-rob-crur/AstroNvim.git`
- TPM dir: `~/.tmux/plugins/tpm`
- Skill source of truth: `dotfiles/.agents/skills`

## Managed links

- `~/.zshrc` -> `dotfiles/.zshrc`
- `~/.p10k.zsh` -> `dotfiles/.p10k.zsh`
- `~/.ideavimrc` -> `dotfiles/.ideavimrc`
- `~/.vimrc` -> `dotfiles/vim/.vimrc`
- `~/.gitconfig` -> `dotfiles/git/.gitconfig`
- `~/.tmux.conf` -> `dotfiles/tmux/.tmux.conf`
- `~/.config/alacritty/alacritty.toml` -> `dotfiles/alacritty/alacritty.toml`
- `~/.config/btop/themes` -> `dotfiles/btop/themes`
- `~/.config/ghostty/config` -> `dotfiles/ghostty/config` on macOS
- `~/.config/ghostty/shaders/unfocused_mute.glsl` -> `dotfiles/ghostty/shaders/unfocused_mute.glsl` on macOS
- `~/.hammerspoon/init.lua` -> `dotfiles/hammerspoon/init.lua` on macOS
- `~/.hammerspoon/edit_anywhere.lua` -> `dotfiles/hammerspoon/edit_anywhere.lua` on macOS
- `~/.config/karabiner/karabiner.json` -> `dotfiles/karabiner/karabiner.json` on macOS only
- `~/.local/share/edit-anywhere/nvim` -> `dotfiles/edit-anywhere/nvim`
- `~/.local/bin/edit-anywhere-nvim` -> `dotfiles/bin/edit-anywhere-nvim`
- `~/.local/bin/edit-anywhere-server` -> `dotfiles/bin/edit-anywhere-server`
- `~/.local/bin/edit-anywhere-spawn` -> `dotfiles/bin/edit-anywhere-spawn`
- `~/.local/bin/edit-anywhere-quick-terminal` -> `dotfiles/bin/edit-anywhere-quick-terminal`
- `~/.local/bin/edit-anywhere-ocr` -> `dotfiles/bin/edit-anywhere-ocr`
- `~/.cache/edit-anywhere/edit-anywhere-ocr-bin` is compiled from the linked OCR source on macOS
- `~/.local/bin/nvim-agent` -> `dotfiles/bin/nvim-agent`
- `~/.local/bin/ssh-fzf` -> `dotfiles/fzf_scripts/ssh-fzf.sh`
- `~/.config/nvim` -> `AstroNvim repo`

## Neovim external context protocol

- `NVIM_EXTERNAL_CONTEXT_FILE`: context file that may appear after Neovim starts
- `NVIM_EXTERNAL_CONTEXT_DELETE=1`: remove the file after loading it
- `NVIM_EXTERNAL_CONTEXT_SOURCE`: context origin such as `window-ocr` or `tmux-shell`
- `NVIM_EXTERNAL_CONTEXT_MAX_LINES`, `NVIM_EXTERNAL_CONTEXT_MAX_CHARS`, and
  `NVIM_EXTERNAL_CONTEXT_MAX_LINE_CHARS`: optional prompt size limits

## Managed tmux helpers

- `<prefix> + M-a` launches `dotfiles/tmux/assistant-launcher.sh` in a popup for temporary Codex/Claude sessions, with foreground and background choices.
- `<prefix> + A` restarts saved Codex/Claude Code panes through `dotfiles/tmux/restart-assistant-panes.py`.
- The assistant launcher starts all Codex/Claude sessions in YOLO/bypass-permissions mode. It uses Codex `model_reasoning_effort`, Claude `--effort`, Claude native `--bg`, and Codex `exec` in a detached tmux window for background tasks.
- Restored Codex sessions are normalized to `--yolo` and restored Claude sessions to `--dangerously-skip-permissions` by both tmux-resurrect and the `<prefix> + A` restart helper; other saved permission overrides are discarded.
- The restart helper depends on the Codex and Claude tmux state caches under `~/.cache/codex-tmux-status` and `~/.cache/claude-tmux-status`.
- Panes without a saved resume id are intentionally skipped to avoid replacing existing context with a new session.

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
- `test -x ~/GitHubRepos/dotfiles/tmux/assistant-launcher.sh`
- `test -x ~/GitHubRepos/dotfiles/tmux/restart-assistant-panes.py`
- `tmux source-file -n ~/.tmux.conf` when tmux is available

## Existing repo notes

- `README.md` and `linux/init_dev.sh` in the dotfiles repo still show the legacy `dotfiles/nvim` link target.
- `macos/init.sh` already has a safe `link_path` helper and is a good reference for backup semantics, but this skill should keep AstroNvim as the live Neovim target.
