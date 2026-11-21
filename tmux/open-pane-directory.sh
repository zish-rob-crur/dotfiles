#!/bin/sh

editor=$(printf '%s\n' 'VS Code' 'Neovim' 'Zed' | FZF_DEFAULT_OPTS='' fzf \
  --layout=reverse --no-multi --prompt='Editor > ' \
  --preview='pwd; echo; ls -1Ap' \
  --preview-window='right:65%:wrap:<80(down:70%:wrap)')
case $? in
  0) ;;
  1|130) exit 0 ;;
  *) exit 1 ;;
esac

case "$editor" in
  'VS Code') exec code . ;;
  Neovim) exec tmux new-window -a -t "$1" -c "$PWD" nvim . ;;
  Zed)
    if command -v zed >/dev/null 2>&1; then
      exec zed .
    fi
    exec open -a Zed .
    ;;
esac
