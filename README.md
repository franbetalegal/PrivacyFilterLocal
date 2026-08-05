# Privacy Filter - Local

100% local PII detection and redaction, powered by OpenAI's Privacy Filter model.
React (Vite) web interface served by a FastAPI backend — packaged as ready-to-run
downloads for **Windows** (a self-extracting `.exe`) and **Linux** (a tarball with
a `run.sh`). End users don't clone the repo or run a build step.

## Features

- **Three-layer detection pipeline** (all local): the OpenAI Privacy Filter
  transformer + deterministic Spanish/Catalan recognizers with check-digit
  validation (DNI, NIE, NIF/CIF, IBAN, Seguridad Social, credit card, phone,
  postal code, plate, cadastral ref) + statistical spaCy NER for names,
  locations and organisations. See [Spanish & Catalan pipeline](#spanish--catalan-pipeline).
- **Consistent pseudonymisation**: every mention of the same entity is replaced
  by the same readable token (`[NOMBRE_1]`, `[DNI_1]`, `[LUGAR_1]`), so the
  redacted document stays coherent for legal review.
- Processes text, PDF, and DOCX files; returns redacted PDF/DOCX.
  - **PDF** redaction maps character offsets back to page rectangles, so PII
    that wraps across lines is still fully covered.
  - **DOCX** redaction reaches every container: body, tables, headers/footers,
    text boxes and comments.
  - **Scanned PDFs** are OCR'd with Tesseract (`spa`/`cat`/`eng`).
- **Post-redaction leak check**: the output is re-extracted and any surviving
  PII string is reported as a warning.
- **Multi-core**: scanned-PDF OCR is parallelised across CPU cores, always
  leaving some free for the host (see [Performance & tuning](#performance--tuning)).
- Runs entirely offline (after the model is downloaded once).
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

For the Spanish/Catalan NER and OCR layers you also need the spaCy models and
the Tesseract binary — see [Spanish & Catalan pipeline](#spanish--catalan-pipeline).
Tests that need them skip automatically when they're absent.

There is also a CLI from the `opf` package: `opf redact "text"`.

## Spanish & Catalan pipeline

On top of the base `opf` model (documented as *primarily English*), the backend
adds two local layers tuned for Spanish/Catalan legal documents:

1. **Deterministic recognizers** (`server/recognizers_es.py`) — regex + check
   digit / checksum validation, so a match is confirmed before it's redacted:
   DNI, NIE, NIF/CIF, IBAN (mod-97), Seguridad Social (mod-97), credit card
   (Luhn), phone, postal code, plate, cadastral reference.
2. **Statistical NER** (`server/ner_es.py`) — spaCy models for person /
   location / organisation. Multiple language models load in parallel; the text
   is split into paragraph blocks and each block is routed to the model that
   matches its detected language, so a Spanish model never tags Catalan common
   words as names (and vice versa) in a bilingual document. A false-positive
   filter drops section headings, form labels, public institutions/regions
   (not personal data), file names and OCR garbage, and trims trailing role
   words (`Mateo Ruiz Cano Domicilio` → `Mateo Ruiz Cano`).

`server/pipeline.py` merges the three sources (deterministic > opf > NER on
overlap) and applies the consistent pseudonymisation.

### System dependencies (Linux server / dev machine)

Beyond `pip install -r requirements-server.txt`, install the spaCy models and
the Tesseract binary + language data:

```bash
# spaCy Spanish + Catalan models (into the same venv)
python -m spacy download es_core_news_lg
python -m spacy download ca_core_news_lg

# Tesseract OCR + Spanish/Catalan traineddata
sudo apt install tesseract-ocr tesseract-ocr-spa tesseract-ocr-cat   # Debian/Ubuntu
brew install tesseract tesseract-lang                                # macOS
```

All of these are imported lazily: if a spaCy model or Tesseract is missing, the
app keeps working with whatever layers are available (a warning is logged), so
they're effectively optional but strongly recommended for Spanish/Catalan docs.

### Configuration (environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `PF_NER_MODELS` | `es_core_news_lg,ca_core_news_lg` | spaCy models to load (comma-separated) |
| `PF_NER_LABELS` | `PER,LOC,ORG` | Entity types to keep from NER |
| `PF_OCR_LANG` | `spa+eng` | Tesseract languages (use `spa+cat+eng` for bilingual) |
| `PF_OCR_DPI` | `300` | Rasterisation DPI for scanned pages |
| `PF_RESERVED_CORES` | `2` | CPU cores always kept free for the host |
| `PF_MAX_WORKERS` | *(unset)* | Optional hard cap on worker processes/threads |

## Performance & tuning

### Hardware support

The app picks its inference path from what the host actually offers, so the
same build runs correctly on Apple Silicon, Intel, AMD and NVIDIA:

| Host | Device | MoE path |
|------|--------|----------|
| NVIDIA GPU + CUDA torch + `triton` | `cuda` | opf's Triton kernels (fastest) |
| NVIDIA GPU without `triton` | `cuda` | portable path, with a warning |
| Intel / AMD CPU | `cpu` | **measured on first run** — see below |
| Apple Silicon | `cpu` | **measured on first run** |
| Apple Silicon, `PF_DEVICE=mps` | `mps` | portable path (slower here; opt-in) |

**Why the MoE path is measured, not assumed.** opf's CPU fallback copies each
token's expert weights and casts them to fp32. On Apple Silicon, where PyTorch
has no fast bf16 GEMM, replacing that with an expert-grouped loop is ~10×
faster in that block and ~8× end-to-end. But a recent Intel Xeon
(AVX-512-BF16, or AMX from Sapphire Rapids) has native bf16 matmul and may be
faster on the *upstream* path. So `server/moe_fast.py` benchmarks both at
startup (sub-second), keeps the winner, and caches the verdict under the
checkpoint keyed by platform + CPU + torch version. Override with
`PF_FAST_MOE=1|0`, re-measure with `PF_MOE_RECALIBRATE=1`.

MPS is available but never auto-selected: measured on an M1, a 10-page document
took ~145 s on MPS versus ~90 s on unoptimised CPU, because the Triton kernels
are CUDA-only and Metal adds kernel-launch overhead on top of the same fallback.

### Where the time goes

Measured per stage on Apple Silicon (M1, 6 worker cores). Model inference
dominates and is **memory-bandwidth-bound**, not compute-bound — verified by
four independent experiments: more torch threads plateau at ~1.4× and then
regress, converting the model to fp32 is 0.76×, a grouped `bmm` in the MoE is
0.7×, and 2–4 worker *processes* are 0.45×/0.32×. Around 1 800 chars/s is this
machine's ceiling; a faster result needs a GPU, not different code.

| Stage | Share | Notes |
|-------|-------|-------|
| opf inference | ~90 % | bandwidth-bound; scales with the machine, not with cores |
| spaCy NER | ~5 % | per-paragraph, language-routed |
| merge + pseudonymisation | ~4 % | O(n log n) overlap sweep |
| deterministic recognizers | <1 % | pure regex |
| PDF OCR (scanned only) | separate | parallel across page ranges |

For a large scanned file, OCR and inference are the two costs that matter;
both are reported per stage in `logs/privacy-filter.log` as `TIMING` lines.

### Core budget

CPU-bound work respects a shared budget of
`cpu_count − PF_RESERVED_CORES` (minimum 1), so a job never starves the host:

- **Scanned-PDF OCR** is split into page ranges and run across worker processes
  (only when a document has ≥3 pages that actually need OCR — text-layer PDFs
  stay sequential, where that's faster). Measured ~3.4× on a 6-page scanned
  document on an 8-core machine; the gain scales with core count, so a many-core
  server benefits more.
- **Model inference** caps `torch` intra-op threads to the same budget.

On a dedicated server, lower `PF_RESERVED_CORES` (e.g. to `1`) to use more
cores; on a shared/desktop machine keep the default so the UI stays responsive.

## Project structure

```
PrivacyFilterLocal/
├── build_portable.ps1      # Builds the portable package into portable-build/
├── packaging/              # Distribution recipes: windows/ (.exe), linux/ (run.sh)
├── .github/workflows/      # CI: build + publish releases on a vX.Y.Z tag
├── server/                 # FastAPI backend
│   ├── main.py             # API routes; serves the React build; logging/diagnostics
│   ├── inference.py        # Model singleton + background download + CPU inference
│   ├── pipeline.py         # Merges opf + deterministic + NER; pseudonymisation
│   ├── recognizers_es.py   # Spanish/Catalan deterministic recognizers + validators
│   ├── ner_es.py           # spaCy NER, per-block language routing, FP filter
│   ├── concurrency.py      # Shared CPU-core budget (PF_RESERVED_CORES)
│   ├── redaction.py        # Text/PDF/DOCX extraction & redaction entry points
│   ├── pdf_ops.py          # PDF char→bbox map, offset redaction, parallel OCR
│   ├── docx_ops.py         # Full-container DOCX traversal (tables/headers/…)
│   ├── verify.py           # Post-redaction leak verification
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
