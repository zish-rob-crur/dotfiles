#!/usr/bin/env bash
set -euo pipefail

force=0
while (($# > 0)); do
  case "$1" in
    --force)
      force=1
      shift
      ;;
    *)
      printf 'usage: %s [--force]\n' "$0" >&2
      exit 2
      ;;
  esac
done

mode="${TMUX_EVERFOREST_MODE:-auto}"
dark_background="${TMUX_EVERFOREST_DARK_BACKGROUND:-medium}"
light_background="github-light-high-contrast"

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

theme_mode="$(detect_mode)"

if [[ "${theme_mode}" == "dark" ]]; then
  theme_background="${dark_background}"
  case "${dark_background}" in
    soft)
      bg="#333c43"
      bg1="#3a464c"
      bg2="#434f55"
      ;;
    *)
      bg="#2d353b"
      bg1="#343f44"
      bg2="#3d484d"
      ;;
  esac

  fg="#d3c6aa"
  muted="#859289"
  accent="#7fbbb3"
  yellow="#dbbc7f"
  selected_fg="${bg}"
else
  theme_background="${light_background}"
  bg="#ffffff"
  bg1="#f6f8fa"
  bg2="#d0d7de"
  fg="#24292f"
  muted="#57606a"
  accent="#0969da"
  yellow="#9a6700"
  selected_fg="#ffffff"
fi

current_mode="$(tmux show -gqv @everforest-mode || true)"
current_background="$(tmux show -gqv @everforest-background || true)"
if [[ "${force}" -eq 0 && "${current_mode}" == "${theme_mode}" && "${current_background}" == "${theme_background}" ]]; then
  exit 0
fi

tmux set -g @everforest-mode "${theme_mode}"
tmux set -g @everforest-background "${theme_background}"

tmux set -g @minimal-tmux-fg "${selected_fg},bold"
tmux set -g @minimal-tmux-bg "${accent}"
tmux set -g window-status-separator "#[fg=${bg2}]│"
tmux set -g @minimal-tmux-status-left-extra " #[none,fg=${bg2}]│#[fg=${accent},bold]#{@pane-layout-signal} #{=/24/...:#{?#{==:#{pane_current_path},#{HOME}},~,#{b:pane_current_path}}}#{?window_zoomed_flag, 󰊓 ,}#{@codex-badge}#[none,fg=${bg2}] │"

minimal_tmx="${HOME}/.tmux/plugins/minimal-tmux-status/minimal.tmux"
if [[ -x "${minimal_tmx}" ]]; then
  "${minimal_tmx}" >/dev/null 2>&1 || true
fi

tmux set -g status-position bottom
tmux set -g status on
tmux set -g status-style "fg=${fg},bg=${bg1}"

tmux set -g window-status-style "fg=${muted},bg=default"
tmux set -g window-status-current-style "fg=${accent},bg=default,bold"
tmux set -g window-status-activity-style "fg=${yellow},bg=default"

tmux set -g message-style "fg=${selected_fg},bg=${accent}"
tmux set -g message-command-style "fg=${fg},bg=${bg2}"
tmux setw -g mode-style "bg=${accent},fg=${selected_fg}"
tmux setw -g copy-mode-selection-style "bg=${yellow},fg=${selected_fg},bold"
tmux set -g clock-mode-colour "${accent}"

tmux set -g window-style "bg=default"
tmux set -g window-active-style "bg=default"
tmux set -g pane-border-style "fg=${bg2},bg=default"
tmux set -g pane-active-border-style "fg=${accent},bg=default,bold"
tmux set -g @zish-pane-label-active-style "fg=${accent},bg=default,bold"
tmux set -g @zish-pane-label-inactive-style "fg=${fg},bg=default"
tmux set -g display-panes-colour "${muted}"
tmux set -g display-panes-active-colour "${accent}"

continuum_save="${HOME}/.tmux/plugins/tmux-continuum/scripts/continuum_save.sh"
if [[ -x "${continuum_save}" ]]; then
  tmux set -g status-right "#(${continuum_save})"
else
  tmux set -g status-right ""
fi
