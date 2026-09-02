# dotfiles

## Edit Anywhere

The reusable Hammerspoon, Ghostty Quick Terminal, OCR, and persistent Neovim
Server integration lives in this repository. See
[`edit-anywhere/README.md`](edit-anywhere/README.md) for installation and
operation, and
[`docs/edit-anywhere-neovim-server.md`](docs/edit-anywhere-neovim-server.md)
for the protocol and safety design.

## Bootstrap

```shell
scripts/bootstrap_dotfiles.sh --mode init
```

This links the managed dotfiles, installs TPM, links AstroNvim from
`~/GithubRepos/zish-rob-crur/AstroNvim`, installs the macOS package list from
`Brewfile`, installs the default Node version through `nvm`, and installs
default Cargo tools such as `spymux`.

For package-only refreshes:

```shell
scripts/install_brewfile_parallel.sh Brewfile
```

This also installs the default Cargo tools after Rust is available.

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
mkdir -p ~/Library/Fonts
cp ~/GithubRepos/dotfiles/fonts/CodexStatusSymbols.ttf ~/Library/Fonts/CodexStatusSymbols.ttf
```

Ghostty expects `Maple Mono NF CN`; `brew bundle --file Brewfile` installs it. `CodexStatusSymbols.ttf` provides the custom Codex and lightweight terminal status icons.

## tmux

Codex and Claude panes expose a compact window badge in the tmux status bar:

- blue `●`: working
- yellow `◆`: waiting for input or approval
- green `󰄬`: finished and not yet viewed
- red `×`: errored

One background refresher runs per tmux server and checks pane titles plus the
bottom of the visible TUI every two seconds. Session-group aliases are deduped
by physical window/pane ID. Finished is acknowledged only after its pane is
selected in a focused tmux client; acknowledgment clears `unread` without
deleting the conversation ID used for recovery.

Codex completion notifications first pass through
`tmux/codex-notify-router.py`. Confirmed subagent completions (and unknown
thread identities) stop before Computer Use/Sky, the sidebar state, the badge,
and desktop notifications. Confirmed root completions are forwarded once;
Codex TUI notifications remain enabled only for approval requests.

An hourly, per-tmux-server daemon parks Codex panes that have stayed at an
empty composer for three days. It skips focused panes, active work, approval
dialogs, typed drafts, and sessions without a verified ID. A parked pane
returns to zsh and prints its session ID plus `codex resume <id>`. Change
`@codex-idle-park-seconds` or `@codex-idle-park-interval` in `.tmux.conf` to
adjust the defaults. Run `tmux/codex-idle-parker.py` for a dry run or add
`--apply` for a one-time cleanup.

From zsh, `cr` opens the current project's Codex resume picker, `crl` resumes
its latest session, and `cra` opens the picker across all projects.

Codex session titles are mirrored into tmux pane labels and the `<prefix> + g`
window/pane picker. Explicitly named sessions take priority; unnamed sessions
use the same generated thread title shown by Codex's resume picker.

tmux-resurrect and `<prefix> + A` restore a Codex/Claude conversation only from
an ID in the current process command or from fresh state that matches the pane,
window, cwd, process lifetime, and tmux server generation. They never infer an
ID from scrollback. Unverified resurrect entries return to a login shell
instead of opening a resume picker. Saved granular permission/tool overrides
such as `--add-dir` and sandbox/approval settings are removed while safe
model/reasoning flags are preserved. Restored Codex sessions are normalized to
`--yolo`, and restored Claude sessions to `--dangerously-skip-permissions`; the
temporary assistant launcher keeps the same bypass-permissions behavior.

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
<Ctrl-b> + C (pick a path, then copy it or open it in a new tmux pane/window with nvim)
```

The same path menu is available from zsh as `cpf [path]`. Use `cpf -a [path]`,
`cpf -r [path]`, or `cpf -f [path]` to directly copy an absolute path, a path
relative to the current directory, or a Finder file object.

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
