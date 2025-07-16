#!/usr/bin/env bash

# 从 ~/.ssh/config 中获取 Host 别名（过滤通配符等）
configHosts=$(grep -E '^Host\s+' ~/.ssh/config 2>/dev/null \
    | awk '{print $2}' \
    | grep -v '[\*\?\!]' \
    | sort -u)

selected=$(
    echo "${configHosts}" \
    | fzf \
        --no-preview \
        --height 40% \
        --border \
        --border-label="SSH Hosts" \
)

if [[ -n "$selected" ]]; then
    ssh "$selected"
fi
