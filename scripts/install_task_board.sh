#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DOTFILES_REPO="${DOTFILES_REPO:-$(cd -P -- "${SCRIPT_DIR}/.." && pwd -P)}"
APP="${HOME}/.local/share/task-board/Task Board.app"
SOURCE="${DOTFILES_REPO}/task-board/main.swift"
PLIST="${DOTFILES_REPO}/task-board/Info.plist"
ICON="${DOTFILES_REPO}/task-board/assets/TaskBoard.png"
TARGET="${APP}/Contents/MacOS/task-board-window"

if [[ "${1:-}" == --dry-run ]]; then
  printf 'Build %s -> %s\n' "$SOURCE" "$APP"
  exit 0
fi
[[ $# == 0 ]] || { printf 'Usage: install_task_board.sh [--dry-run]\n' >&2; exit 2; }
[[ "$(uname -s)" == Darwin ]] || exit 0
mkdir -p "${HOME}/.local/bin"
FOCUS_LINK="${HOME}/.local/bin/focus-notes"
FOCUS_SOURCE="${DOTFILES_REPO}/tmux/focus_notes.py"
if [[ ! -L "$FOCUS_LINK" || "$(readlink "$FOCUS_LINK")" != "$FOCUS_SOURCE" ]]; then
  if [[ -e "$FOCUS_LINK" || -L "$FOCUS_LINK" ]]; then
    mv "$FOCUS_LINK" "${FOCUS_LINK}.bak.$(date +%Y%m%d%H%M%S)"
  fi
  ln -s "$FOCUS_SOURCE" "$FOCUS_LINK"
fi
if [[ -x "$TARGET" && "$TARGET" -nt "$SOURCE" && "${APP}/Contents/Info.plist" -nt "$PLIST" && "${APP}/Contents/Resources/TaskBoard.icns" -nt "$ICON" ]]; then
  printf 'Task Board window is current: %s\n' "$APP"
  exit 0
fi
mkdir -p "${APP}/Contents/MacOS" "${APP}/Contents/Resources"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/task-board-build.XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT
/usr/bin/swiftc -O "$SOURCE" -o "${STAGING}/task-board-window"
mkdir "${STAGING}/TaskBoard.iconset"
for size in 16 32 128 256 512; do
  /usr/bin/sips -z "$size" "$size" "$ICON" --out "${STAGING}/TaskBoard.iconset/icon_${size}x${size}.png" >/dev/null
  /usr/bin/sips -z "$((size * 2))" "$((size * 2))" "$ICON" --out "${STAGING}/TaskBoard.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "${STAGING}/TaskBoard.iconset" -o "${STAGING}/TaskBoard.icns"
# Replace only after a successful build; never truncate a running executable.
mv "${STAGING}/TaskBoard.icns" "${APP}/Contents/Resources/TaskBoard.icns"
cp "$PLIST" "${APP}/Contents/Info.plist"
mv "${STAGING}/task-board-window" "$TARGET"
printf 'Installed Task Board window: %s\n' "$APP"
