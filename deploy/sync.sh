#!/usr/bin/env bash
# 把外接硬盘上的源码同步到内置磁盘的运行副本，然后重启服务
# 原因：launchd 后台进程无权访问 /Volumes 外接卷，运行时必须放在内置磁盘
set -euo pipefail

SRC="/Volumes/HP P900/数字孪生/branch-assistant"
DST="$HOME/xiaohui-assistant"

rsync -a --delete \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude 'data/' \
  "$SRC/" "$DST/"

# 知识库与配置同步，但运行期数据（审计留痕、公网地址）保留在运行副本
for f in "$SRC"/data/*.json; do
  cp "$f" "$DST/data/"
done

chmod +x "$DST/deploy/serve.sh" "$DST/deploy/tunnel.sh"
echo "已同步到 $DST"

launchctl kickstart -k "gui/$(id -u)/com.xiaohui.assistant" 2>/dev/null || true
echo "服务已重启，地址：http://127.0.0.1:8000"
