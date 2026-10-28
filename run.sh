#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
python3 -m pip install -q -r requirements.txt
exec env PORT="$PORT" python3 app.py
