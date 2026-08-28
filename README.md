# Privacy Filter - Local

100% local PII detection and redaction, powered by OpenAI's Privacy Filter model.
React (Vite) web interface served by a FastAPI backend — packaged as ready-to-run
downloads for **Windows** (a self-extracting `.exe`), **macOS Apple Silicon**
(a tarball with a double-clickable `run.command`) and **Linux** (a tarball with
a `run.sh`). End users don't clone the repo or run a build step.

## Features

- **Three-layer detection pipeline** (all local): the OpenAI Privacy Filter
  transformer + deterministic Spanish/Catalan recognizers with check-digit
  validation (DNI, NIE, NIF/CIF, IBAN, Seguridad Social, credit card, phone,
  postal code, plate, cadastral ref) plus shape-matched street addresses and
  verification codes + statistical spaCy NER for names and locations.
  See [Spanish & Catalan pipeline](#spanish--catalan-pipeline).
- **Tuned on real administrative documents.** Names written in caps inside a
  form header, surnames that collide with contract vocabulary ("Banco",
  "Construcción" are real Spanish surnames), and addresses hard-wrapped across
  two lines are all detected — the cases a paragraph-level model misses because
  PDF extraction cuts entities in half and puts unrelated form fields side by
  side.
- **Códigos Seguros de Verificación are removed.** A CSV is not another
  identifier, it is a credential: with it anyone can fetch the original from the
  issuing body's website, so an "anonymised" copy carrying its own CSV can be
  traded back for the original.
- **Redacts what identifies, keeps what the document is about.** Dates of birth
  go; acquisition and transmission dates stay, because a capital gain is computed
  from them. Company and institution names stay too. The result is a copy you can
  hand to a model to reason about the matter without handing over the person.
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
  PII string is reported as a warning. On a scanned PDF the check cannot see
  inside the image, and says so rather than reporting a clean result.
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

> Nothing else has to be installed: the package carries its own Python and its
> own Tesseract, so OCR of scanned PDFs works out of the box. The first run
> downloads the detection models (~2.7 GB for the PII model plus ~1.2 GB for the
> Spanish/Catalan name models); progress is shown in the app, which stays on its
> preparing screen until every layer is in place.
> SmartScreen may warn about the unsigned `.exe` the first time — choose
> **More info → Run anyway**.

### macOS (Apple Silicon)
1. Download **`PrivacyFilter-macos-arm64-vX.Y.Z.tar.gz`** and double-click it
   to extract.
2. **First time only:** right-click on `run.command` → **Open** → **Open**.
   macOS asks because the build is not signed with an Apple Developer ID;
   after the first launch it stops asking.
3. Later on, just double-click `run.command`. The first run creates a local
   virtualenv and downloads the detection models (~2.7 GB for the PII model
   plus ~1.2 GB for the Spanish/Catalan name models, progress shown in the
   app).

Requires macOS 12+ (Apple Silicon) and nothing else. The archive carries its
own Python and its own Tesseract, so OCR of scanned PDFs works out of the box.

### Linux (technical users)
1. Download **`PrivacyFilter-linux-vX.Y.Z.tar.gz`** and extract it.
2. Run `./run.sh`. The first run creates a local virtualenv, installs the
   dependencies and downloads the detection models (~2.7 GB for the PII model
   plus ~1.2 GB for the Spanish/Catalan name models); later runs just start the
   app and open your browser.

The archive carries its own Python and its own Tesseract, so nothing has to be
installed first and OCR of scanned PDFs works out of the box. The bundled
Tesseract is built on Ubuntu 24.04 and ships the libraries it links against; on
a much older distribution it may refuse to load, and the launcher then says so
and points at `apt install tesseract-ocr`.

### All platforms
- **Stop**: the **Quit** button in the UI (the server keeps running if you only
  close the browser tab).
- **Something wrong?** Use **Diagnostics** in the UI to download a support bundle.
  On Windows you can also run `start.bat` to see the server in a console. Logs live
  under `logs/privacy-filter.log` inside the app folder.

Everything (models, caches, temp, logs) stays inside the app folder and the
server binds to `127.0.0.1` only. Delete the folder to uninstall.

**The app will not open until every detection layer is present.** The name
models are what find a person written in caps — the form every Spanish tax and
court document uses — so running without them returns documents that look
anonymised and are not. Releases up to 2.6.3 did exactly that; see the 2.7.0
entry in [CHANGELOG.md](CHANGELOG.md). The **Información** tab lists the state
of each component, including OCR, which does not block anything because it only
affects scanned documents.

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
package + `requirements-server.txt`, bundles Tesseract (the binary it finds
installed, or one it installs with Chocolatey, plus `spa`/`cat`/`eng` from
`tessdata_fast`), builds/uses `frontend/dist`, copies the backend, and writes
the launchers into `portable-build/`.

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

For macOS Apple Silicon, do the same but ship `packaging/macos/run.command`
instead of `run.sh`. Building must be done on an actual arm64 Mac (or the
`macos-14` GitHub runner) so the bundled path picks up the right Homebrew
Python; `.github/workflows/release.yml` does exactly that.

**Releases are automated:** pushing a `vX.Y.Z` tag triggers
`.github/workflows/release.yml`, which builds the Windows `.exe`, the macOS
arm64 tarball, and the Linux
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

   Two entities have no check digit and earn their place differently. A **street
   address** must show two independent signals, a street-type marker *and* a
   house number or `s/n` — a marker alone matches `Cl@ve PIN`, the tax agency's
   login system, and any sentence opening with "Plaza". A **Código Seguro de
   Verificación** is the only recognizer that *requires* context: its shape is
   shared with cadastral references and hashes, so the announcing words have to
   be nearby. Once confirmed anywhere in a document, the same code is redacted
   at its other occurrences too, which is how the copy on the signature page —
   where nothing announces it — stops shipping the credential.
2. **Statistical NER** (`server/ner_es.py`) — spaCy models for person and
   location. Multiple language models load in parallel; the text is split into
   paragraph blocks and each block is routed to the model that matches its
   detected language, so a Spanish model never tags Catalan common words as
   names (and vice versa) in a bilingual document.

   Every block is analysed **twice over, and at two granularities**. All-caps
   runs are title-cased for a second pass, because the models are case-sensitive
   and return fragments for a name written in capitals. And each line is
   analysed on its own as well as within its paragraph: in a form the adjacent
   lines are unrelated fields, and at paragraph level the model merges them into
   one entity that the lexicon then rejects wholesale — which is how a name in a
   page header went undetected in ten of its eleven occurrences before this.

   A false-positive filter drops section headings, form labels, public
   institutions/regions (not personal data), URLs, tax and accounting
   vocabulary, file names and OCR garbage, and trims role words off both ends
   (`Nicolau Ferrera Bosch Domicilio` → `Nicolau Ferrera Bosch`). A lone
   capitalised word needs a person trigger in front of it to count as a name,
   and conversely a surname that collides with contract vocabulary — "Banco",
   "Construcción" and "Contrato" are real Spanish surnames — is rescued when
   one does.

`server/pipeline.py` merges the three sources (deterministic > opf > NER on
overlap) and applies the consistent pseudonymisation.

### The NER models

They are **not** a pip dependency and are not bundled in any build: they are
~1.2 GB of data, so `server/ner_models.py` downloads them into the app folder
on first run, exactly like the opf checkpoint, and validates each one by
opening it before it replaces anything. On every start the app re-checks
spaCy's own compatibility table (`spacy.about.__compatibility__`) and installs
a newer compatible version if one has been published.

The startup preflight (`server/inference.run_preflight`) keeps the app
unavailable until they are installed, `/api/health` reports the state of every
component, and `smoke_ner.py` fails a release build whose environment cannot
detect a name written in caps. Before 2.7.0 none of that existed and the models
were only mentioned in a comment in `requirements-server.txt`, so no build ever
had them.

On a development machine a pip-installed model is used as-is and nothing is
re-downloaded, so `python -m spacy download es_core_news_lg` remains a valid
setup.

### System dependencies (Linux server / dev machine)

Beyond `pip install -r requirements-server.txt`, you can install the spaCy
models yourself (optional — the app fetches them if you do not) and the
Tesseract binary + language data:

```bash
# spaCy Spanish + Catalan models (into the same venv)
python -m spacy download es_core_news_lg
python -m spacy download ca_core_news_lg

# Tesseract OCR + Spanish/Catalan traineddata
sudo apt install tesseract-ocr tesseract-ocr-spa tesseract-ocr-cat   # Debian/Ubuntu
brew install tesseract tesseract-lang                                # macOS
```

Tesseract is imported lazily and stays optional: without it, pages with no text
layer extract as nothing, which the UI now says out loud instead of reporting a
document with no detections. The spaCy models are not optional — the app
installs them itself and will not serve a document until they are in place.

### Configuration (environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `PF_NER_MODELS` | `es_core_news_lg,ca_core_news_lg` | spaCy models to load and keep installed (comma-separated) |
| `PF_NER_DIR` | `<app folder>/ner-models` | Where the downloaded spaCy models live |
| `PF_NER_LABELS` | `PER,LOC` | Entity types to keep from NER. Add `ORG` to redact company and institution names too — off by default because an entity name keeps the document readable and rarely protects a natural person |
| `PF_REDACT_ALL_DATES` | `0` | Redact every date instead of only dates of birth. Off by default: an acquisition or transmission date is what a tax computation rests on |
| `PF_OCR_LANG` | `spa+cat+eng` | Tesseract languages. The builds ship data for exactly these three |
| `PF_TESSDATA_DIR` | *(set by the launchers)* | Language data for the bundled Tesseract, passed as `--tessdata-dir`. Unset means a system install resolves its own |
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
│   ├── ner_models.py       # Downloads/validates/updates the spaCy models
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

- **PII model**: the **Update model** button (Info tab) downloads the latest
  model.
- **NER models**: checked on every start and upgraded on their own when a newer
  version compatible with the installed spaCy is published. The install is
  staged and validated first, so a bad download never costs the working model.
- **App**: the **Update app** banner installs the latest release in place. On
  macOS and Linux it downloads that platform's archive and unpacks it over the
  install, keeping the models, caches and virtualenv; in a git checkout it
  pulls. Windows installs are replaced by re-running the installer, which
  leaves the runtime folders (`model/`, `ner-models/`, `cache/`, `logs/`)
  alone. Either way the pinned dependencies are reinstalled afterwards, and an
  update whose reinstall fails is reported as failed rather than as a
  successful update with a footnote.

## License

Apache 2.0 — see [LICENSE](privacy-filter/LICENSE). Based on
[OpenAI Privacy Filter](https://github.com/openai/privacy-filter).
