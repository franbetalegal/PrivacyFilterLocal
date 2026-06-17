# Privacy Filter - Local

100% local PII detection and redaction, powered by OpenAI's Privacy Filter model.
React (Vite) web interface served by a FastAPI backend — and packaged as a
**portable**, self-contained Windows app.

## Features

- Detects 8 types of PII: names, emails, phones, addresses, dates, URLs, account
  numbers, and secrets
- Processes text, PDF, and DOCX files; returns redacted PDF/DOCX
- Runs entirely offline (after the model is downloaded once)
- **Portable**: a single folder with an embedded Python — no Python/Node install
  on the target machine, and nothing written outside the folder
- First-run model download and any errors are shown **in the app** (no console)
- Model updates from HuggingFace

## For end users — run the portable app

1. Get the `portable-build` folder (built as below) and copy it anywhere.
2. Double-click **`Privacy Filter.vbs`**.
3. The app opens in your browser automatically. The first run downloads the model
   (~2.7 GB); progress is shown in the app.

- **Stop**: the **Quit** button in the UI.
- **Something wrong?** Use **Diagnostics** in the UI to download a support bundle,
  or run `start.bat` to see the server in a console. Logs: `logs\privacy-filter.log`.

Everything (model, caches, temp, logs) stays inside the folder; binds to
`127.0.0.1` only. Delete the folder to uninstall.

## Build the portable package

Run on a build machine with **Python** and **Node.js** (Node is only needed to
build the React UI; the end user never needs it).

```powershell
git clone https://github.com/franbetalegal/PrivacyFilterLocal.git
cd PrivacyFilterLocal
.\build_portable.ps1                 # output: .\portable-build\
.\build_portable.ps1 -IncludeModel   # also bundle the model (fully offline, ~4 GB)
.\build_portable.ps1 -RebuildFrontend  # force-rebuild the UI (after UI changes)
```

The script downloads an embeddable Python, installs CPU-only PyTorch + the `opf`
package + `requirements-server.txt`, builds/uses `frontend/dist`, copies the
backend, and writes the launchers into `portable-build/`. Distribute that folder.

## Develop

```powershell
# Backend
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e .\privacy-filter
pip install -r requirements-server.txt
python -m uvicorn server.main:app --host 127.0.0.1 --port 7860

# Frontend (separate terminal; proxies /api to :7860)
corepack pnpm -C frontend install
corepack pnpm -C frontend dev        # http://localhost:5173
# or build once and let FastAPI serve it:
corepack pnpm -C frontend run build

# Tests
pip install -r requirements-dev.txt ; python -m pytest tests/
```

There is also a CLI from the `opf` package: `opf redact "text"`.

## Project structure

```
PrivacyFilterLocal/
├── build_portable.ps1      # Builds the portable package into portable-build/
├── server/                 # FastAPI backend
│   ├── main.py             # API routes; serves the React build; logging/diagnostics
│   ├── inference.py        # Model singleton + background download + CPU inference
│   ├── redaction.py        # Text/PDF/DOCX extraction & redaction
│   └── updates.py          # Model/app update orchestration
├── frontend/               # React + Vite UI (src/; dist/ is generated)
├── app_update.py           # App version check (GitHub Releases)
├── model_update.py         # Model download/update (HuggingFace; honors OPF_CHECKPOINT)
├── create_release.py       # GitHub release helper
├── requirements-server.txt # Backend deps (single source)
├── requirements-dev.txt    # + pytest
├── tests/                  # pytest
├── VERSION / CHANGELOG.md
├── privacy-filter/         # Core OPF package (opf/, pyproject.toml)
└── portable-build/         # Generated portable package (git-ignored)
```

## Updating

- **Model**: the **Update model** button (Info tab) downloads the latest model.
- **App**: distribute a freshly built `portable-build/`. (The in-app self-update
  via git does not apply to the portable package.)

### Creating a release
```powershell
$env:GITHUB_TOKEN = "your-token"; python create_release.py; Remove-Item Env:GITHUB_TOKEN
```

## License

Apache 2.0 — see [LICENSE](privacy-filter/LICENSE). Based on
[OpenAI Privacy Filter](https://github.com/openai/privacy-filter).
