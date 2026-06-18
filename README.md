# Privacy Filter - Local

100% local PII detection and redaction, powered by OpenAI's Privacy Filter model.
React (Vite) web interface served by a FastAPI backend — packaged as ready-to-run
downloads for **Windows** (a self-extracting `.exe`) and **Linux** (a tarball with
a `run.sh`). End users don't clone the repo or run a build step.

## Features

- Detects 8 types of PII: names, emails, phones, addresses, dates, URLs, account
  numbers, and secrets
- Processes text, PDF, and DOCX files; returns redacted PDF/DOCX
- Runs entirely offline (after the model is downloaded once)
- **Portable on Windows**: a single folder with an embedded Python — no Python/Node
  install on the target machine, and nothing written outside the folder
- **Runs on Linux too**: a small tarball + `run.sh` that creates a local virtualenv
  on first run (technical users can also `pip install` from source)
- First-run model download and any errors are shown **in the app** (no console)
- Model updates from HuggingFace

## For end users — download and run

Grab the latest build from the
[Releases page](https://github.com/franbetalegal/PrivacyFilterLocal/releases).

### Windows
1. Download **`PrivacyFilter-Setup-vX.Y.Z.exe`**.
2. Put it in a folder of its own and double-click it.
3. It extracts the app into a **`PrivacyFilter`** subfolder next to the `.exe`
   and opens automatically in your browser.

> The first run downloads the model (~2.7 GB); progress is shown in the app.
> SmartScreen may warn about the unsigned `.exe` the first time — choose
> **More info → Run anyway**.

### Linux (technical users)
1. Download **`PrivacyFilter-linux-vX.Y.Z.tar.gz`** and extract it.
2. Run `./run.sh`. The first run creates a local virtualenv and installs the
   dependencies; later runs just start the app and open your browser.

Requires Python 3.10+ (`python3` and `python3-venv`).

### Both platforms
- **Stop**: the **Quit** button in the UI (the server keeps running if you only
  close the browser tab).
- **Something wrong?** Use **Diagnostics** in the UI to download a support bundle.
  On Windows you can also run `start.bat` to see the server in a console. Logs live
  under `logs/privacy-filter.log` inside the app folder.

Everything (model, caches, temp, logs) stays inside the app folder and the server
binds to `127.0.0.1` only. Delete the folder to uninstall.

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
backend, and writes the launchers into `portable-build/`.

Wrap that folder into the single self-extracting `.exe`:

```powershell
.\packaging\windows\make_exe.ps1            # -> dist\PrivacyFilter-Setup-vX.Y.Z.exe
.\packaging\windows\make_exe.ps1 -Build     # build the portable folder first
```

(needs [NSIS](https://nsis.sourceforge.io): `choco install nsis`).

For Linux, build the UI once (`corepack pnpm -C frontend run build`) and ship
`server/`, `privacy-filter/`, `frontend/dist`, `app_update.py`, `model_update.py`,
`requirements-server.txt`, `VERSION` and `packaging/linux/run.sh` together as a
tarball.

**Releases are automated:** pushing a `vX.Y.Z` tag triggers
`.github/workflows/release.yml`, which builds both the Windows `.exe` and the Linux
tarball and attaches them to the GitHub Release. Manual builds are only for local
testing.

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
├── packaging/              # Distribution recipes: windows/ (.exe), linux/ (run.sh)
├── .github/workflows/      # CI: build + publish releases on a vX.Y.Z tag
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
Bump `VERSION` + `CHANGELOG.md`, commit, then tag and push — CI builds the
artifacts and publishes the release:
```powershell
git tag v2.4.0 ; git push origin v2.4.0
```
`create_release.py` remains for creating a release entry manually via the API:
```powershell
$env:GITHUB_TOKEN = "your-token"; python create_release.py; Remove-Item Env:GITHUB_TOKEN
```

## License

Apache 2.0 — see [LICENSE](privacy-filter/LICENSE). Based on
[OpenAI Privacy Filter](https://github.com/openai/privacy-filter).
