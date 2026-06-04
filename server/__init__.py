"""FastAPI backend for Privacy Filter - Local.

Wraps the existing Python logic (the ``opf`` PyTorch model, PyMuPDF/python-docx
redaction, and the app/model update modules) behind a small HTTP API and serves
the React frontend. Replaces the previous Gradio interface.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The project root holds app_update.py / model_update.py; the ``opf`` package
# lives in the ``privacy-filter`` subdirectory. Make both importable, mirroring
# the path setup the old app_local.py used.
PROJECT_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = PROJECT_DIR / "privacy-filter"

for _p in (str(PROJECT_DIR), str(REPO_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
