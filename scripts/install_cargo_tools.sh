#!/usr/bin/env bash

set -euo pipefail

CARGO_TOOLS=(
    spymux
)

resolve_cargo() {
    if [ -n "${CARGO:-}" ] && [ -x "${CARGO}" ]; then
        printf '%s\n' "${CARGO}"
    elif command -v cargo >/dev/null 2>&1; then
        command -v cargo
    elif [ -x "${HOME}/.cargo/bin/cargo" ]; then
        printf '%s\n' "${HOME}/.cargo/bin/cargo"
    elif [ -x /opt/homebrew/bin/cargo ]; then
        printf '%s\n' /opt/homebrew/bin/cargo
    elif [ -x /usr/local/bin/cargo ]; then
        printf '%s\n' /usr/local/bin/cargo
    else
        return 1
    fi
}

is_cargo_package_installed() {
    local package="$1"

    "${CARGO_BIN}" install --list 2>/dev/null \
        | awk -v package="${package}" '$1 == package { found = 1 } END { exit found ? 0 : 1 }'
}

CARGO_BIN="$(resolve_cargo)" || {
    echo "cargo is not available on PATH; install Rust first" >&2
    exit 1
}

for tool in "${CARGO_TOOLS[@]}"; do
    if is_cargo_package_installed "${tool}"; then
        echo "Cargo tool already installed: ${tool}"
        continue
    fi

    "${CARGO_BIN}" install "${tool}"
done
