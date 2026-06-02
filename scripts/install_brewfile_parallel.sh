#!/usr/bin/env bash

set -euo pipefail

BREWFILE="${1:-Brewfile}"
BREW_JOBS="${BREW_JOBS:-4}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

if [ ! -f "${BREWFILE}" ]; then
    echo "Brewfile not found: ${BREWFILE}" >&2
    exit 1
fi

if [ -x /opt/homebrew/bin/brew ]; then
    BREW=/opt/homebrew/bin/brew
elif [ -x /usr/local/bin/brew ]; then
    BREW=/usr/local/bin/brew
elif command -v brew >/dev/null 2>&1; then
    BREW="$(command -v brew)"
else
    echo "Homebrew is not available on PATH" >&2
    exit 1
fi

install_taps() {
    awk -F'"' '/^tap "/ { print $2 }' "${BREWFILE}" | while IFS= read -r tap_name; do
        [ -n "${tap_name}" ] || continue
        "${BREW}" tap "${tap_name}"
    done
}

install_formulae() {
    local formulae
    formulae="$(awk -F'"' '/^brew "/ { print $2 }' "${BREWFILE}")"
    [ -n "${formulae}" ] || return 0

    # shellcheck disable=SC2086
    "${BREW}" install ${formulae}
}

install_casks_parallel() {
    local casks
    casks="$(awk -F'"' '/^cask "/ { print $2 }' "${BREWFILE}")"
    [ -n "${casks}" ] || return 0

    printf '%s\n' "${casks}" \
        | xargs -P "${BREW_JOBS}" -I {} bash -c '
            for attempt in 1 2 3; do
                "$0" install --cask "$1" && exit 0
                sleep 5
            done
            exit 1
        ' "${BREW}" {}
}

install_cargo_tools() {
    "${SCRIPT_DIR}/install_cargo_tools.sh"
}

"${BREW}" update
install_taps
install_formulae
install_casks_parallel
install_cargo_tools
