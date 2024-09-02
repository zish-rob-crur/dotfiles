#!/usr/bin/env bash
# tmux workspace search
# 2. 创建一个 fzf 窗口 用于搜索文件夹 忽略 .git node_modules .venv venv 

selected_file=$(fd -t d -E .git -E node_modules -E .venv -E venv -E 'Library/' -d 5 . ~/ \
| fzf  --reverse --border --ansi --prompt="Search workspace: " \
--preview="tree -C {} | head -200" --tmux 80%,50% 
)
if [ -z "$selected_file" ]; then
    exit 0
fi

selected_option=$(echo -e "current tmux window\n\
new tmux window\n\
new tmux pane\n\
vscode\n\
new tmux window & vscode\n\
zed\n\
vim\n\
new tmux window & vim"\
| fzf --reverse --border --ansi --prompt="Open with: " --tmux 50%,50%  --preview="echo $selected_file"
)

case $selected_option in
    "current tmux window")
        tmux send-keys -t 0 "cd $selected_file" Enter
        ;;
    "new tmux window")
        tmux new-window -c $selected_file
        ;;
    "new tmux pane")
        tmux split-window -c $selected_file
        ;;
    "vscode")
        code $selected_file
        ;;
    "new tmux window & vscode")
        tmux new-window -c $selected_file
        code $selected_file
        ;;
    "zed")
        zed $selected_file
        ;;
    "vim")
        tmux send-keys -t 0 "nvim $selected_file" Enter
        ;;
    "new tmux window & vim")
        tmux new-window -c $selected_file
        tmux send-keys -t 0 "nvim $selected_file" Enter
        ;;
esac