#!/usr/bin/env bash

set -u

usage() {
  cat <<'EOF'
Usage: tmux-reset

Back up tmux-resurrect snapshots and stop every tmux session. The command
requires explicit confirmation and keeps tmux-resurrect enabled for the
new, clean server.
EOF
}

if (( $# > 0 )); then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'tmux-reset: unsupported argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
fi

if ! command -v tmux >/dev/null 2>&1; then
  printf '%s\n' 'tmux-reset: tmux is not installed.' >&2
  exit 1
fi

tmux_running=0
if tmux list-sessions >/dev/null 2>&1; then
  tmux_running=1
fi

configured_dir=""
if (( tmux_running )); then
  configured_dir="$(tmux show-options -gqv @resurrect-dir 2>/dev/null || true)"
fi

if [[ -n "$configured_dir" ]]; then
  host_name="$(hostname)"
  resurrect_dir="${configured_dir//\$HOME/$HOME}"
  resurrect_dir="${resurrect_dir//\$HOSTNAME/$host_name}"
  resurrect_dir="${resurrect_dir/#\~/$HOME}"
elif [[ -d "$HOME/.tmux/resurrect" ]]; then
  resurrect_dir="$HOME/.tmux/resurrect"
else
  resurrect_dir="${XDG_DATA_HOME:-$HOME/.local/share}/tmux/resurrect"
fi

if [[ "$resurrect_dir" != /* || "$resurrect_dir" == / || "$resurrect_dir" == "$HOME" ]]; then
  printf 'tmux-reset: refusing unsafe resurrect directory: %s\n' "$resurrect_dir" >&2
  exit 1
fi

snapshot_count=0
if [[ -d "$resurrect_dir" ]]; then
  snapshot_count="$(find "$resurrect_dir" -maxdepth 1 -type f -name 'tmux_resurrect_*.txt' -print 2>/dev/null | wc -l | tr -d '[:space:]')"
fi

printf '%s\n' 'tmux-reset will:'
printf '  - Stop automatic saving for the current tmux server\n'
printf '  - Move %s restore snapshots out of: %s\n' "$snapshot_count" "$resurrect_dir"
printf '  - Close every tmux session\n'
printf '%s\n' 'Snapshots will be kept in a timestamped backup. The next tmux server will start clean.'

printf 'Type RESET to continue: '
IFS= read -r confirmation
if [[ "$confirmation" != RESET ]]; then
  printf '%s\n' 'Confirmation did not match. Cancelled.'
  exit 0
fi

if (( tmux_running )); then
  if ! tmux set-option -g @continuum-save-interval 0; then
    printf '%s\n' 'tmux-reset: could not stop automatic saving; nothing was changed.' >&2
    exit 1
  fi

  for _ in {1..20}; do
    if ! pgrep -f '[/]tmux-resurrect/scripts/save.sh' >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done

  if pgrep -f '[/]tmux-resurrect/scripts/save.sh' >/dev/null 2>&1; then
    printf '%s\n' 'tmux-reset: a tmux-resurrect save is still running; nothing was changed.' >&2
    exit 1
  fi
fi

backup_dir=""
if [[ -d "$resurrect_dir" ]]; then
  backup_dir="${resurrect_dir}.backup-$(date '+%Y%m%d-%H%M%S')"
  if [[ -e "$backup_dir" ]]; then
    printf 'tmux-reset: backup target already exists: %s\n' "$backup_dir" >&2
    exit 1
  fi
  if ! mv "$resurrect_dir" "$backup_dir"; then
    printf '%s\n' 'tmux-reset: could not back up the restore directory; tmux was not stopped.' >&2
    exit 1
  fi
  printf 'Restore snapshots were backed up to: %s\n' "$backup_dir"
else
  printf '%s\n' 'No restore snapshot directory was found.'
fi

if (( tmux_running )); then
  printf '%s\n' 'Stopping every tmux session...'
  exec tmux kill-server
else
  printf '%s\n' 'tmux is not running; restore state has been cleaned.'
fi
