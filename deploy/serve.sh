#!/usr/bin/env bash
# 生产模式启动（gunicorn）。1 个进程 + 多线程：会话、取号、审计状态在进程内共享，避免多 worker 状态分裂。
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"
THREADS="${THREADS:-8}"
TIMEOUT="${TIMEOUT:-60}"

exec gunicorn app:app \
  --bind "${HOST}:${PORT}" \
  --workers "${WORKERS}" \
  --threads "${THREADS}" \
  --timeout "${TIMEOUT}" \
  --access-logfile - \
  --error-logfile - \
  --log-level info \
  --forwarded-allow-ips '*'
