#!/usr/bin/env bash
set -euo pipefail

mode="${GIT_DELTA_THEME_MODE:-auto}"

detect_mode() {
  if [[ "${mode}" == "dark" || "${mode}" == "light" ]]; then
    printf '%s\n' "${mode}"
    return
  fi

  if [[ "$(uname -s)" == "Darwin" ]] && defaults read -g AppleInterfaceStyle 2>/dev/null | grep -q Dark; then
    printf 'dark\n'
  else
    printf 'light\n'
  fi
}

if [[ "$(detect_mode)" == "dark" ]]; then
  exec delta --dark --syntax-theme gruvbox-dark --features everforest-dark "$@"
else
  exec delta --light --syntax-theme GitHub --features github-light-primer "$@"
fi
