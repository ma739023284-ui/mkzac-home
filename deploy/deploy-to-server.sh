#!/usr/bin/env bash
# 从本机一键部署到阿里云 ECS（或任意 Linux 服务器）
# 用法: ./deploy/deploy-to-server.sh root@47.100.x.x
set -euo pipefail

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  echo "用法: ./deploy/deploy-to-server.sh root@<服务器公网IP>"
  echo "示例: ./deploy/deploy-to-server.sh root@47.100.12.34"
  exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="/opt/xiaohui-assistant"

echo "==> 检查服务器连通性与 Docker"
if ! ssh -o ConnectTimeout=10 -o BatchMode=no "$TARGET" 'true'; then
  echo "无法连接到 $TARGET，请检查 IP、密码/密钥和 22 端口安全组"
  exit 1
fi

if ! ssh "$TARGET" 'command -v docker >/dev/null 2>&1'; then
  echo "==> 服务器未安装 Docker，开始安装（约需 1-3 分钟）"
  ssh "$TARGET" 'curl -fsSL https://get.docker.com | sh && systemctl enable --now docker'
fi

echo "==> 上传代码到 $REMOTE_DIR"
rsync -az --delete \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude 'data/audit.jsonl' \
  --exclude 'data/public_url.txt' \
  --exclude 'deploy/certs/' \
  "$SRC/" "$TARGET:$REMOTE_DIR/"

echo "==> 构建并启动容器"
ssh "$TARGET" "cd $REMOTE_DIR && { [ -f .env ] || cp .env.example .env; } && docker compose up -d --build"

echo "==> 健康检查"
ssh "$TARGET" 'sleep 6; curl -sS -m 5 http://127.0.0.1:8000/api/health; echo'

echo
echo "部署完成。下一步："
echo "  1. 未备案/无证书阶段，用 HTTP 访问：http://<服务器公网IP>"
echo "  2. 域名备案通过后，上传证书到 $REMOTE_DIR/deploy/certs/"
echo "     然后执行：ssh $TARGET 'cd $REMOTE_DIR && docker compose up -d --force-recreate nginx'"
