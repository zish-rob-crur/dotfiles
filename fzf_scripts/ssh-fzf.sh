#!/usr/bin/env bash

# 从 ~/.ssh/config 中收集 Host 别名（过滤掉一些可能包含通配符等的条目）
configHosts=$(grep -E '^Host\s+' ~/.ssh/config 2>/dev/null \
    | awk '{print $2}' \
    | grep -v '[\*\?\!]' \
    | sort -u)

# 从 ~/.ssh/known_hosts 中提取主机名（去除花括号、IP、端口等）
knownHosts=$(cut -d',' -f1 ~/.ssh/known_hosts 2>/dev/null \
    | cut -d' ' -f1 \
    | sed 's/\[//g; s/\]//g' \
    | sort -u)

# 合并并去重
allHosts=$(echo -e "${configHosts}\n${knownHosts}" | sort -u)

# 调用 fzf 进行模糊搜索
selected=$(echo "${allHosts}" | fzf --prompt="Select SSH host> ")

# 若用户成功选择了某个主机，则使用 ssh 登录
if [[ -n "$selected" ]]; then
    ssh "$selected"
fi
