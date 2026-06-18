#!/usr/bin/env bash
# Privacy Filter - Local : Linux launcher.
# ---------------------------------------------------------------------------
# On first run this creates a self-contained virtualenv next to this script
# (CPU-only PyTorch + the `opf` model package + the FastAPI backend), then
# starts the local server and opens your browser. Everything (venv, model,
# caches, temp, logs) stays inside this folder and the server binds to
# 127.0.0.1 only. To uninstall, delete the folder.
#
# Usage:
#   ./run.sh            # first run sets things up; later runs just start it
#   PF_PORT=8000 ./run.sh
#   PYTHON=python3.12 ./run.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PY="${PYTHON:-python3}"
VENV="$DIR/.venv"
VENV_PY="$VENV/bin/python"
DEPS_OK="$VENV/.deps-ok"
PORT="${PF_PORT:-7860}"

# Keep all runtime data inside this folder (mirrors the Windows portable build).
export OPF_CHECKPOINT="$DIR/model"
export HF_HOME="$DIR/cache/huggingface"
export HUGGINGFACE_HUB_CACHE="$DIR/cache/huggingface/hub"
export TIKTOKEN_CACHE_DIR="$DIR/cache/tiktoken"
export TMPDIR="$DIR/tmp"
export PF_LOG_DIR="$DIR/logs"
export PF_HOST="127.0.0.1"
export PF_PORT="$PORT"
mkdir -p "$OPF_CHECKPOINT" "$HF_HOME" "$TIKTOKEN_CACHE_DIR" "$TMPDIR" "$PF_LOG_DIR"

# Return 0 if something is already listening on the local port.
port_in_use() {
  "$PY" - "$PORT" <<'PYEOF' 2>/dev/null
import socket, sys
s = socket.socket(); s.settimeout(1)
rc = s.connect_ex(("127.0.0.1", int(sys.argv[1]))); s.close()
sys.exit(0 if rc == 0 else 1)
PYEOF
}

open_url() {
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:$PORT" >/dev/null 2>&1 || true
  fi
}

# Wait (background) until the server answers, then open the browser.
open_when_ready() {
  local i
  for i in $(seq 1 180); do
    if port_in_use; then open_url; return 0; fi
    sleep 0.5
  done
}

# Single instance: if it is already running, just open the browser and stop.
if port_in_use; then
  echo "Privacy Filter is already running on port $PORT. Opening your browser..."
  open_url
  exit 0
fi

# First-run setup: build the virtualenv and install dependencies.
if [ ! -f "$DEPS_OK" ]; then
  if ! command -v "$PY" >/dev/null 2>&1; then
    echo "ERROR: '$PY' not found. Install Python 3.10+ (e.g. 'sudo apt install python3 python3-venv') and retry." >&2
    exit 1
  fi
  echo "First run: setting up the environment (downloads PyTorch + deps, a few minutes)..."
  "$PY" -m venv "$VENV"
  "$VENV_PY" -m pip install --upgrade pip
  "$VENV_PY" -m pip install torch --index-url https://download.pytorch.org/whl/cpu
  "$VENV_PY" -m pip install "$DIR/privacy-filter"
  "$VENV_PY" -m pip install -r "$DIR/requirements-server.txt"
  touch "$DEPS_OK"
  echo "Setup complete."
fi

echo "Starting Privacy Filter at http://localhost:$PORT  (Ctrl+C to stop)"
echo "The first launch downloads the PII model (~2.7 GB); progress shows in the app."
open_when_ready &
exec "$VENV_PY" -m server.main
