#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: scripts/install_status_font.sh [--dry-run] [--font-dir DIR]

Build and install Codex Status Symbols. Requires uv; dependencies are pinned
in build_codex_status_font.py. The default font directory is ~/Library/Fonts.
USAGE
}

DRY_RUN=0
FONT_DIR="${HOME}/Library/Fonts"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --font-dir)
            if [ "$#" -lt 2 ] || [ -z "$2" ]; then
                echo "--font-dir requires a directory" >&2
                exit 1
            fi
            FONT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
FONT_PATH="${FONT_DIR}/CodexStatusSymbols.ttf"

if command -v uv >/dev/null 2>&1; then
    UV="$(command -v uv)"
elif [ -x /opt/homebrew/bin/uv ]; then
    UV=/opt/homebrew/bin/uv
elif [ -x /usr/local/bin/uv ]; then
    UV=/usr/local/bin/uv
elif [ "${DRY_RUN}" -eq 1 ]; then
    UV=uv
else
    echo "uv is required to build the status font. Install it with brew install uv; --skip-brew requires an existing uv installation." >&2
    exit 1
fi

if [ "${DRY_RUN}" -eq 1 ]; then
    printf 'DRY-RUN:'
    printf ' %q' "${UV}" run --python 3.13 --script "${SCRIPT_DIR}/build_codex_status_font.py" --output '<temporary-directory>/CodexStatusSymbols.ttf'
    printf '\n'
    printf 'DRY-RUN: install font at %s (skip identical bytes; back up an existing different font)\n' "${FONT_PATH}"
    exit 0
fi

BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/codex-status-font.XXXXXX")"
trap 'rm -rf "${BUILD_DIR}"' EXIT
BUILT_FONT="${BUILD_DIR}/CodexStatusSymbols.ttf"

"${UV}" run --python 3.13 --script "${SCRIPT_DIR}/build_codex_status_font.py" --output "${BUILT_FONT}"

if [ -f "${FONT_PATH}" ] && [ ! -L "${FONT_PATH}" ] && cmp -s "${BUILT_FONT}" "${FONT_PATH}"; then
    echo "Already installed font: ${FONT_PATH}"
    exit 0
fi

mkdir -p "${FONT_DIR}"
if [ -e "${FONT_PATH}" ] || [ -L "${FONT_PATH}" ]; then
    BACKUP="${FONT_PATH}.bak.$(date +%Y%m%d%H%M%S).$$"
    mv "${FONT_PATH}" "${BACKUP}"
    echo "Backed up font: ${BACKUP}"
fi

cp "${BUILT_FONT}" "${FONT_PATH}"
echo "Installed font: ${FONT_PATH}"
