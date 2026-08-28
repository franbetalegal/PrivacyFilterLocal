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

# Everything extracted from a downloaded archive inherits com.apple.quarantine,
# and macOS kills an unsigned quarantined binary on exec — which would take the
# bundled Python and Tesseract down with it, with no message the user can act
# on. Clearing it on our own folder is what the user would otherwise do by hand,
# once, through the Gatekeeper dialog. Best effort: a failure here is not fatal.
if [ -z "${PF_SKIP_QUARANTINE_CLEAR:-}" ]; then
  xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true
fi

# The archive ships its own relocatable CPython, so nothing has to be
# installed on the machine first. PYTHON= still overrides it, and a build
# without the bundled runtime falls back to the system python3.
BUNDLED_PY="$DIR/python/bin/python3"
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x "$BUNDLED_PY" ]; then
  PY="$BUNDLED_PY"
else
  PY="python3"
fi
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
# Bundled Tesseract: on PATH so both pytesseract and the app's own component
# check find it, with its language data pointed at explicitly. A copy already
# installed on the machine still wins if the user puts it earlier on PATH.
if [ -x "$DIR/tesseract/bin/tesseract" ]; then
  export PATH="$DIR/tesseract/bin:$PATH"
  export PF_TESSDATA_DIR="$DIR/tesseract/tessdata"
fi
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

# Only reachable when the bundled runtime is absent (a hand-assembled folder,
# or PYTHON= pointing somewhere wrong). A release archive carries its own.
if ! command -v "$PY" >/dev/null 2>&1 && [ ! -x "$PY" ]; then
  cat >&2 <<EOF
ERROR: No se encuentra Python ($PY).
Este paquete debería traer el suyo en python/bin/python3. Si lo has borrado o
has usado PYTHON= para apuntar a otro, corrige eso; si no, vuelve a descargar
el paquete.
EOF
  read -n 1 -s -r -p "Pulsa una tecla para cerrar…"
  exit 1
fi

# OCR of scanned PDFs. The archive ships Tesseract, so this only fires on a
# build assembled without it; the app reports the same thing in its interface.
if ! command -v tesseract >/dev/null 2>&1; then
  cat >&2 <<'EOF'

Aviso: no se encuentra Tesseract y este paquete no lo trae. El OCR de PDFs
escaneados no funcionará: un PDF sin capa de texto se leerá vacío. La app se
abrirá igualmente para documentos con texto seleccionable.

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
