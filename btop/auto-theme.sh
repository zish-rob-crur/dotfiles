#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/btop"
CONFIG_FILE="${BTOP_CONFIG_FILE:-$CONFIG_DIR/btop.conf}"
DARK_THEME="${BTOP_DARK_THEME:-flexoki-dark.theme}"
LIGHT_THEME="${BTOP_LIGHT_THEME:-flexoki-light.theme}"

usage() {
  cat >&2 <<EOF
Usage: ${SCRIPT_NAME} [--sync-only] [BTOP_ARGS...]

Sync btop's color_theme with the current system appearance, then launch btop.
Override detection with BTOP_THEME_MODE=dark|light.
EOF
}

detect_mode() {
  case "${BTOP_THEME_MODE:-auto}" in
    dark|light)
      printf '%s\n' "${BTOP_THEME_MODE}"
      return
      ;;
    auto|"") ;;
    *)
      printf '%s: invalid BTOP_THEME_MODE: %s\n' "${SCRIPT_NAME}" "${BTOP_THEME_MODE}" >&2
      exit 2
      ;;
  esac

  if [[ "$(uname -s)" == "Darwin" ]] && defaults read -g AppleInterfaceStyle 2>/dev/null | grep -q Dark; then
    printf 'dark\n'
  else
    printf 'light\n'
  fi
}

ensure_config() {
  local theme="$1"

  mkdir -p "${CONFIG_DIR}"
  if [[ ! -f "${CONFIG_FILE}" ]]; then
    cat >"${CONFIG_FILE}" <<EOF
#? Config file for btop

color_theme = "${theme}"
theme_background = True
truecolor = True
EOF
    return
  fi

  if grep -q '^color_theme[[:space:]]*=' "${CONFIG_FILE}"; then
    THEME="${theme}" perl -0pi -e 's/^color_theme\s*=\s*".*?"/color_theme = "$ENV{THEME}"/m' "${CONFIG_FILE}"
  else
    printf '\ncolor_theme = "%s"\n' "${theme}" >>"${CONFIG_FILE}"
  fi
}

sync_only=0
while (($# > 0)); do
  case "$1" in
    --sync-only)
      sync_only=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

mode="$(detect_mode)"
case "${mode}" in
  dark) theme="${DARK_THEME}" ;;
  light) theme="${LIGHT_THEME}" ;;
esac

ensure_config "${theme}"

if [[ "${sync_only}" == "1" ]]; then
  printf '%s\n' "${theme}"
  exit 0
fi

exec btop "$@"
