#!/usr/bin/env bash
# tmux workspace search
# 2. 创建一个 fzf 窗口 用于搜索文件夹 忽略 .git node_modules .venv venv 

# Function to sanitize and handle paths with special characters like spaces
selected_file=$(fd -t d -E .git -E node_modules -E .venv -E venv -E 'Library/' -E 'Applications/' -d 5 . ~/ \
| fzf  --reverse --border --ansi --prompt="Search workspace: " \
--preview="tree -C {} | head -200" --tmux 80%,50% 
)

if [ -z "$selected_file" ]; then
    exit 0
fi

# Array of actions
actions=(
    "current tmux window"
    "new tmux window"
    "new tmux pane"
    "vscode"
    "new tmux window & vscode"
    "zed"
    "vim"
    "new tmux window & vim"
    "finder"
    "copy path"
)
selected_option=$(printf "%s\n" "${actions[@]}" | fzf --reverse --border --ansi --prompt="Open with: " --tmux 30%,50% --preview="echo $selected_file" --preview-window=down:3:wrap)

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
    "finder")
        open $selected_file
        ;;
    "copy path")
        echo $selected_file | pbcopy
        ;;
esac
