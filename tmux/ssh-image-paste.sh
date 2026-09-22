#!/usr/bin/env bash
# prefix+P: put the clipboard image where the current pane can reach it and
# paste its path into the pane, over ssh as well.
#
# A program on the far side of an ssh session cannot read this machine's
# clipboard, and a terminal carries text, not images. A path it can open is
# enough: a coding agent (Codex, Claude Code) attaches an image whose path is
# pasted into its prompt, and a shell command takes it as an argument.
#
#     pngpaste -> $TMPDIR/clipboard-image-<stamp>.png
#     ssh <host> "cat > ~/.cache/clipboard-images/<stamp>.png"   (ssh panes only)
#     tmux paste-buffer -p  ->  the path arrives as a paste
#
#     ssh-image-paste.sh [pane]           attach to that pane (default: current)
#     ssh-image-paste.sh --target [pane]  print the ssh target of the pane, if any
#     ssh-image-paste.sh --host <target>  copy to that host and print the remote
#                                         path (no tmux; used by the ⌘⇧I hotkey
#                                         for an ssh session in a bare terminal)
set -euo pipefail

export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"  # tmux run-shell starts with a bare PATH

REMOTE_DIR='$HOME/.cache/clipboard-images'
KEEP_DAYS=7
SSH=/usr/bin/ssh  # macOS ssh: the keychain options in ~/.ssh/config are its own
SSH_OPTIONS=(-o ControlMaster=auto -o "ControlPath=$HOME/.ssh/.clipboard-image-%r@%h:%p" -o ControlPersist=60)

say() { tmux display-message "$1"; }

# The ssh command running in the pane, looked for a few process levels down
# (the shell's child, or a child of that when ssh runs inside a wrapper).
ssh_command_of_pane() {
  local pids command
  pids=$(tmux display -p -t "$1" '#{pane_pid}' 2>/dev/null) || return 1
  for _ in 1 2 3; do
    [[ -n "$pids" ]] || return 1
    local next=""
    for pid in $pids; do
      command=$(ps -o command= -p "$pid" 2>/dev/null || true)
      if [[ "$command" == ssh\ * ]]; then
        printf '%s' "$command"
        return 0
      fi
      next+=" $(pgrep -P "$pid" 2>/dev/null || true)"
    done
    pids=$next
  done
  return 1
}

# [user@]host from an ssh command line: the first argument that is neither an
# option nor the value of one.
target_of_ssh_command() {
  local -a words
  read -r -a words <<<"$1"
  local skip=0
  for word in "${words[@]:1}"; do
    if (( skip )); then skip=0; continue; fi
    case "$word" in
      -[bcDEeFIiJLlmOopQRSWw]) skip=1 ;;
      -o) skip=1 ;;
      -*) ;;
      *) printf '%s' "$word"; return 0 ;;
    esac
  done
  return 1
}

pane_target() {
  local command
  command=$(ssh_command_of_pane "$1") || return 1
  target_of_ssh_command "$command"
}

# --host prints the remote path for a caller that does its own pasting.
host_only=""
if [[ "${1:-}" == "--host" ]]; then
  host_only=${2:-}
  [[ -n "$host_only" ]] || { echo "usage: ssh-image-paste.sh --host <target>" >&2; exit 64; }
fi

pane=${2:-${1:-}}
[[ "$pane" == "--target" || -n "$host_only" ]] && pane=""
pane=${pane:-$(tmux display -p '#{pane_id}')}

if [[ "${1:-}" == "--target" ]]; then
  pane_target "$pane" || echo "(local)"
  exit 0
fi

fail() {  # a hotkey caller reads stderr; inside tmux the status line is the place for it
  if [[ -n "$host_only" ]]; then echo "$1" >&2; else say "$1"; fi
  exit "${2:-1}"
}

command -v pngpaste >/dev/null 2>&1 || fail "pngpaste 未安装：brew install pngpaste"

stamp=$(date +%Y%m%d-%H%M%S)
local_file="${TMPDIR:-/tmp}"; local_file="${local_file%/}/clipboard-image-${stamp}.png"  # TMPDIR ends in / on macOS
pngpaste "$local_file" >/dev/null 2>&1 || fail "剪贴板里没有图片"

copy_to_host() {
  "$SSH" "${SSH_OPTIONS[@]}" "$1" \
    "dir=\"$REMOTE_DIR\"; mkdir -p \"\$dir\"; find \"\$dir\" -type f -mtime +$KEEP_DAYS -delete 2>/dev/null; \
     file=\"\$dir/clipboard-image-${stamp}.png\"; cat > \"\$file\" && printf %s \"\$file\"" < "$local_file"
}

if [[ -n "$host_only" ]]; then
  remote_file=$(copy_to_host "$host_only") || fail "传输到 ${host_only} 失败"
  rm -f "$local_file"
  printf '%s\n' "$remote_file"
  exit 0
fi

if target=$(pane_target "$pane"); then
  say "正在把图片传到 ${target} …"
  remote_file=$(copy_to_host "$target") || fail "传输到 ${target} 失败"
  rm -f "$local_file"
  path=$remote_file
else
  path=$local_file  # a local pane: Codex reads this path just as well
fi

tmux set-buffer -b clipboard-image -- "$path"
tmux paste-buffer -p -d -b clipboard-image -t "$pane"
say "已粘贴图片路径：$path"
