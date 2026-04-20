---
name: dotfiles-init-update
description: Initialize or refresh zish dotfiles and AstroNvim, including relinking home configs and ensuring TPM is installed.
---

# Dotfiles Init Update

Bootstrap and refresh zish local shell/editor/tmux config from the personal dotfiles repo and the personal AstroNvim repo. Prefer the bundled script for any repo, backup, or symlink change so behavior stays consistent and reviewable.

## Workflow

1. Read `references/environment.md` for the canonical repo paths, remotes, and managed links.
2. Inspect the current state before changing anything:
   - `git -C ~/GitHubRepos/dotfiles status -sb` when the repo exists
   - `git -C ~/GitHubRepos/zish-rob-crur/AstroNvim status -sb` when the repo exists
   - `readlink ~/.config/nvim`
   - `test -d ~/.tmux/plugins/tpm`
3. Choose a mode:
   - `init` when one or both repos are missing, a managed symlink is missing, or the machine has not been bootstrapped yet.
   - `update` when the repos already exist and the goal is to pull the latest changes, refresh submodules, and re-assert links.
4. Dry-run first:
   - `scripts/bootstrap_dotfiles.sh --mode init --dry-run`
   - `scripts/bootstrap_dotfiles.sh --mode update --dry-run`
5. If the dry-run plan matches the request, rerun without `--dry-run`.
6. Validate after execution:
   - `readlink ~/.config/nvim`
   - `readlink ~/.tmux.conf`
   - `readlink ~/.zshrc`
   - `test -d ~/.tmux/plugins/tpm`
   - `readlink ~/.config/ghostty/config` on macOS
7. Report any skipped repo updates. The script intentionally refuses to `pull --ff-only` on dirty repos.

## Behavior Rules

- Treat `~/GitHubRepos/zish-rob-crur/AstroNvim` as the default Neovim target.
- Do not use the dotfiles repo's legacy `dotfiles/nvim` link path for this skill.
- Do not delete `~/.local/share/nvim`, `~/.local/state/nvim`, or other Neovim caches unless the user explicitly asks for a clean reset.
- Backup conflicting files and directories by renaming them with a timestamp suffix before creating a managed symlink.
- Stop if an expected repo path already exists but is not a git repository. Do not overwrite unknown directories.
- For package-manager work such as `brew install` or `apt install`, prefer the repo's existing platform bootstrap scripts only when the user explicitly wants package installation. This skill's bundled script focuses on repo sync, symlinks, TPM, Ghostty, and AstroNvim.

## Script Entry Point

Use `scripts/bootstrap_dotfiles.sh`.

Common commands:

```bash
scripts/bootstrap_dotfiles.sh --mode init --dry-run
scripts/bootstrap_dotfiles.sh --mode init
scripts/bootstrap_dotfiles.sh --mode update --dry-run
scripts/bootstrap_dotfiles.sh --mode update
```

Useful overrides:

```bash
scripts/bootstrap_dotfiles.sh \
  --mode init \
  --dotfiles-repo "$HOME/GitHubRepos/dotfiles" \
  --astro-repo "$HOME/GitHubRepos/zish-rob-crur/AstroNvim"
```

The script also honors `DOTFILES_REPO`, `DOTFILES_REMOTE`, `ASTRO_REPO`, `ASTRO_REMOTE`, and `TPM_DIR`.

## Resources

- `scripts/bootstrap_dotfiles.sh`: Idempotent init/update orchestration with timestamped backups and dry-run support.
- `references/environment.md`: Canonical paths, remotes, managed links, and platform notes.
