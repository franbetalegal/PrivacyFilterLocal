#!/usr/bin/env bash
# Privacy Filter - Local : macOS launcher (Apple Silicon).
# ---------------------------------------------------------------------------
# Double-click this file from Finder. On first run it creates a self-contained
# virtualenv next to itself (with Apple-Silicon PyTorch + the `opf` model
# package + the FastAPI backend), then starts the local server and opens your
# default browser. Everything (venv, model, caches, temp, logs) stays inside
# this folder and the server binds to 127.0.0.1 only. To uninstall, delete the
# folder.
#
# First run only, macOS will refuse to open a downloaded .command with a
# Gatekeeper warning ("cannot verify the developer"). Right-click the file →
# Open → Open. After the first launch macOS remembers and no more prompts.
#
# Usage from a terminal (optional):
#   ./run.command               # first run sets things up; later runs start it
#   PF_PORT=8000 ./run.command
#   PYTHON=python3.12 ./run.command
set -euo pipefail

# When launched from Finder the working directory is the user's home. Change
# to the folder that contains this script so venv/cache/logs land here.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PY="${PYTHON:-python3}"
VENV="$DIR/.venv"
VENV_PY="$VENV/bin/python"
DEPS_OK="$VENV/.deps-ok"
PORT="${PF_PORT:-7860}"

# Keep every runtime path inside this folder so uninstalling is "delete the
# folder", same guarantee the Windows portable build and the Linux tarball give.
export OPF_CHECKPOINT="$DIR/model"
export HF_HOME="$DIR/cache/huggingface"
export HUGGINGFACE_HUB_CACHE="$DIR/cache/huggingface/hub"
export TIKTOKEN_CACHE_DIR="$DIR/cache/tiktoken"
export TMPDIR="$DIR/tmp"
export PF_LOG_DIR="$DIR/logs"
export PF_DATA_DIR="$DIR/data"
# spaCy NER models (person names). Downloaded on first run like the PII model,
# not shipped in the archive: ~1.2 GB that would otherwise sit in every
# download. Without them, names written in caps are not detected at all.
export PF_NER_DIR="$DIR/ner-models"
export PF_HOST="127.0.0.1"
export PF_PORT="$PORT"
mkdir -p "$OPF_CHECKPOINT" "$HF_HOME" "$TIKTOKEN_CACHE_DIR" "$TMPDIR" \
         "$PF_LOG_DIR" "$PF_DATA_DIR" "$PF_NER_DIR"

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
  # `open` is the macOS equivalent of xdg-open.
  open "http://localhost:$PORT" >/dev/null 2>&1 || true
}

# Wait (background) until the server answers, then open the browser.
open_when_ready() {
  local i
  for i in $(seq 1 180); do
    if port_in_use; then open_url; return 0; fi
    sleep 0.5
  done
}

# Single-instance behavior: if it's already running, just open the browser.
if port_in_use; then
  echo "Privacy Filter ya está en marcha en el puerto $PORT. Abriendo el navegador…"
  open_url
  exit 0
fi

# On macOS Python may not be preinstalled with a working version. Point users
# to the right fix instead of failing with a cryptic 'not found'.
if ! command -v "$PY" >/dev/null 2>&1; then
  cat >&2 <<EOF
ERROR: No se encuentra Python.
Instálalo con Homebrew (recomendado):

  /bin/bash -c "\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  brew install python@3.12

Luego vuelve a hacer doble-clic sobre run.command.
EOF
  read -n 1 -s -r -p "Pulsa una tecla para cerrar…"
  exit 1
fi

# Tesseract is required for OCR of scanned PDFs. Warn (don't fail): the app
# still works on text-layer documents without it.
if ! command -v tesseract >/dev/null 2>&1; then
  cat >&2 <<'EOF'

Aviso: no se encuentra Tesseract. El OCR de PDFs escaneados no funcionará.
Para instalarlo:
  brew install tesseract tesseract-lang

Puedes hacerlo más tarde; la app se abrirá igualmente para documentos con
capa de texto.

EOF
fi

if [ ! -f "$DEPS_OK" ]; then
  echo "Primer arranque: preparando el entorno (descarga PyTorch + dependencias, unos minutos)…"
  "$PY" -m venv "$VENV"
  "$VENV_PY" -m pip install --upgrade pip
  # PyTorch on Apple Silicon: the default macOS wheel already includes MPS
  # support. No special --index-url needed (that one is Linux-CPU-only).
  "$VENV_PY" -m pip install torch
  "$VENV_PY" -m pip install "$DIR/privacy-filter"
  "$VENV_PY" -m pip install -r "$DIR/requirements-server.txt"
  touch "$DEPS_OK"
  echo "Preparación completada."
fi

echo "Abriendo Privacy Filter en http://localhost:$PORT   (Ctrl+C para detener)"
echo "La primera vez se descarga el modelo (~2,7 GB); verás el progreso en la app."
open_when_ready &
exec "$VENV_PY" -m server.main
