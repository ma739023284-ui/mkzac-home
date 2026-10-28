#!/usr/bin/env bash
# 公网隧道守护：断线自动重连，并把最新公网地址写入 data/public_url.txt
set -uo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
URL_FILE="$PWD/data/public_url.txt"
mkdir -p "$(dirname "$URL_FILE")"

while true; do
  echo "[$(date '+%F %T')] 正在连接 serveo ..."
  ssh -T \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
      -o ServerAliveInterval=15 \
      -o ServerAliveCountMax=3 \
      -o ConnectTimeout=20 \
      -o ExitOnForwardFailure=yes \
      -R "80:localhost:${PORT}" serveo.net 2>&1 | \
  while IFS= read -r line; do
    echo "[$(date '+%F %T')] $line"
    url=$(printf '%s' "$line" | grep -oE 'https://[A-Za-z0-9._-]+\.(serveousercontent\.com|serveo\.net)' | head -1)
    if [ -n "${url:-}" ]; then
      printf '%s\n' "$url" > "$URL_FILE"
      echo "[$(date '+%F %T')] 当前公网地址已更新：$url"
    fi
  done
  echo "[$(date '+%F %T')] 隧道断开，5 秒后重连"
  sleep 5
done
