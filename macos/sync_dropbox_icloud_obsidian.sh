#!/usr/bin/env bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

SRC="/Users/zhiwen.wang/Library/CloudStorage/Dropbox/obsidian"
DST="/Users/zhiwen.wang/Library/Mobile Documents/iCloud~md~obsidian/Documents/obsidian"

COMMON_OPTS="--fast-list --progress -v"
BISYNC_OPTS="--checksum \
             --conflict-resolve=newer \
             --conflict-loser=pathname \
             --max-delete 30"   # 允许一次性删掉最多 30% 的对象

INIT=false
while [[ $# -gt 0 ]]; do
  case $1 in
    -i|--init|--first-run) INIT=true ;;
    -h|--help) echo "用法: $0 [--init]"; exit 0 ;;
    *) echo "未知参数 $1"; exit 1 ;;
  esac; shift
done

if $INIT; then
  echo "🚀 首次 / 重建状态：--resync"
  rclone bisync "$SRC" "$DST" $COMMON_OPTS $BISYNC_OPTS --resync
else
  echo "🔄 增量同步"
  rclone bisync "$SRC" "$DST" $COMMON_OPTS $BISYNC_OPTS
fi