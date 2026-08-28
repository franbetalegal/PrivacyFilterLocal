# Changelog

All notable changes to Privacy Filter Local will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.6.3] - 2026-08-28

### Fixed
- **Clicking a detection mode's name now selects it.** Only the radio circle
  itself responded, so the mode looked stuck unless you hit a target a few
  pixels wide. The cause was not the styling but a hardcoded `name="mode"`:
  since a visited tab stays mounted, the Text and Files tabs can both be in the
  DOM, and with no form to separate them all six radios formed a single group.
  A browser allows one checked radio across a group while React forces one per
  selector, so the DOM and the state drifted apart and the click looked ignored.
  Each selector now gets its own group, the pill grew into its hit target
  instead of being padded for its text alone, and the selected mode is marked
  with a background and border rather than a hairline outline.

- **The server now restarts itself after an app update.** The update installed,
  the log said so, and nothing came back up. The restart spawned a child process
  and killed the parent, but the detached-process flag only applies on Windows:
  on macOS and Linux the child inherited the process group of the terminal that
  launched the app, so the hangup raised by the parent's exit took it down too.
  The child was also started before the parent released the port, and the port
  fallback in the launcher never ran because uvicorn exits rather than raising
  when it cannot bind.

  The backend now replaces its own process image (`os.execv`), which keeps the
  PID, the session and the launcher's window, orphans nothing, and frees the
  listening socket so the new process binds the same port — the one the browser
  already has open. It behaves the same under run.command, run.sh, a plain
  `python -m server.main` and the Windows portable, so no launcher changed.

  Note for anyone updating *from* 2.6.2: that jump still runs the old restart
  code, so the app has to be closed and reopened by hand one last time. Updates
  from 2.6.3 onwards restart on their own.

- **The page reloads itself once the new server answers.** The banner used to
  show the backend's English "Restarting in 2 seconds…" and then sit against a
  server that was gone. `/api/health` now carries an instance id and the banner
  waits for a *different* one before reloading: the outgoing process still
  answers for a second or two, so polling for mere reachability would reload the
  very version the update replaced. After sixty seconds it says so instead of
  spinning.

## [2.6.2] - 2026-08-27

### Fixed
- **A name alone on a header line is now found on scanned documents.** Measured
  on a real 4-page scanned court order: the person named on the
  "MENOR: <name>" line was never redacted there, though the same person was
  redacted elsewhere in the document. Joined into one long line, that name sits
  in a header soup ("... Expediente 4242/2016 <field label> MENOR: <name> <next
  heading> ...") and spaCy proposes nothing at all for the region; given the
  page's own line breaks, the line is analysed on its own and the name comes
  out. The addressee's name came out whole too, instead of as two wrong
  fragments, and so did the address.

  Line breaks only. Block and paragraph boundaries deliberately do NOT become
  blank lines, which is what 2.6.1 did and what made it worse: Tesseract splits
  a noisy scan into blocks freely — a watermark or a column becomes its own
  block — and a blank line there cuts the context both models depend on.
  `PF_OCR_LINE_BREAKS=0` restores the previous whole-page-as-one-line behaviour.

- **Case numbers no longer depend on how the OCR laid out the page.** They were
  being detected by the transformer reading the surrounding line, so the same
  number was found when the page arrived as one long line and lost when it
  arrived as separate lines. A number that identifies a proceeding — and through
  it the person it concerns — should not be at the mercy of layout, so
  `ES_CASE_NUMBER` now matches it deterministically: "N° Expediente Fiscalía:
  3141/2015", "Procedimiento Expediente 4242/2016", "Diligencias Previas
  1234/2019".

  Only the number is matched, never the announcing word, so the redacted
  document still reads "N° Expediente Fiscalía: [EXPEDIENTE_1]". A trigger word
  is required nearby, and recognizers gained an `exclude_context` so statute
  citations are never touched: "Ley Orgánica 5/2000" and "Real Decreto
  1774/2004" are cited constantly in exactly these documents and are public law,
  not anybody's personal data.

- **A scanned document could lose most of its pages to a dead worker pool.**
  Parallel page extraction is an optimisation, but a worker dying takes the
  whole pool down with it (`BrokenProcessPool`) and every pending page with it,
  and the exception propagated straight out of `extract_with_map`, past its own
  `Optional` contract. Reproduced on the same 4-page scan — just over the
  3-OCR-page threshold that turns the pool on — where each spawned worker
  re-imports the server package, torch included, and the machine could not carry
  four of those at once, while the very same pages extract fine one at a time.
  A failed pool is now logged, discarded so the next document retries in
  parallel, and the document is extracted sequentially instead.

## [2.6.1] - 2026-08-27

### Fixed
- **Names on scanned documents were missed on some pages.** OCRed pages were
  handed to the analyzer as one very long line: `_ocr_page` joined every word
  on the page with single spaces and appended a single newline at the end, while
  the text-layer path has always emitted a newline per line and a second one per
  block. That silenced the per-line NER pass (`ner_es._iter_segments`), which
  only yields lines when the block contains a newline — and it is the pass that
  catches a name sitting alone on a header line, exactly the shape that was
  surviving redaction on real scanned court documents. Tesseract already
  reports the block, paragraph and line of every word, so the separators are
  now read from it rather than guessed. Separators carry no rectangle, so the
  character-to-coordinate map stays 1:1 and redaction still lands on the right
  glyphs.

- **The in-app updater could not update a macOS or Linux install.** It scanned
  the release for an asset ending in `.zip`; releases have only ever carried
  `.tar.gz` (macOS, Linux) and `.exe` (Windows). So the archive path was
  unreachable and every install fell through to `git pull`, which cannot work
  in a folder extracted from a tarball — the very thing the archives are for.
  The asset is now chosen by platform, `.tar.gz` is unpacked (with tar's `data`
  filter, which refuses path traversal and links escaping the destination), and
  the bundled `opf` package plus the pinned requirements are reinstalled
  afterwards so an update cannot ship new sources while the venv keeps running
  the old wheel. When there is genuinely nothing installable for the platform,
  the app now says where to download it instead of surfacing a git error about
  a directory that is not a repository.

- **The update check never reached GitHub on macOS.** A python.org framework
  Python ships no CA bundle until the user runs `Install Certificates.command`,
  so every `urlopen` failed with CERTIFICATE_VERIFY_FAILED — and because the
  check reports network errors only in its `error` field, which nothing
  surfaces, the app simply never showed an update. Measured on a real 2.6.0
  install: the check had never once succeeded. Both update checks now verify
  against `certifi`'s bundle, the same one `requests` uses, which is why the
  model download worked while the update check did not. `certifi` is pinned in
  `requirements-server.txt` now that it is a direct dependency.

- **Leaving a tab threw away the work in it.** Tabs were unmounted on every
  switch, so leaving the Files tab mid-analysis discarded the file, the detected
  spans and the review selection while the upload carried on server-side with
  nowhere to land. The "save as evaluation example" checkbox could never take
  effect either, because the example is saved in a second step that needed the
  file the unmount had already discarded. Tabs are now mounted on first open
  and hidden rather than unmounted; mounting stays lazy, so opening the app does
  not fire every tab's start-up requests at once.

## [2.5.0] - 2026-08-05

### Added
- **Optional Markdown export of the redacted document.** A new checkbox in the
  Files tab, *"Convertir también a Markdown (para pegar en una IA)"*, produces
  a `.md` alongside the redacted PDF/DOCX when ticked. Aimed at the workflow
  where the user pastes the anonymised document into an LLM for summarisation
  or drafting: Markdown uses far fewer tokens than a PDF (which the model
  otherwise rasterises or extracts with layout noise) and preserves the
  document structure (headings, lists, tables), which measurably improves the
  model's answers with less context.

  The Markdown is generated from the already-redacted output, never the
  original, so no sensitive data crosses the converter. For scanned PDFs whose
  redacted copy is images without a text layer, the pipeline's own
  already-substituted plain text is served as the `.md` instead — same
  placeholders, no structural recovery, but the user still gets a token-
  efficient artefact.

  Under the hood: `markitdown[pdf,docx,pptx,xlsx,xls,outlook]` (selective
  extras, never `[all]`) with `llm_client=None` and plugin discovery disabled,
  so no code path in the package can talk to the network.
  `tests/test_offline_guarantee.py` fails loudly if that ever slips.

## [2.4.0] - 2026-06-18

### Added
- **One-click distribution.** Releases now ship ready-to-run downloads, so end
  users no longer clone the repo or run a build command:
  - **Windows:** a self-extracting `PrivacyFilter-Setup-vX.Y.Z.exe`. Double-click
    it and it extracts the app into a `PrivacyFilter` subfolder **next to the
    `.exe`** and launches automatically. The runtime folders (model/cache/temp/
    logs) are not packed, so re-running it refreshes the app without wiping an
    already-downloaded model.
  - **Linux (technical users):** a `PrivacyFilter-linux-vX.Y.Z.tar.gz`. Extract
    and run `./run.sh`, which creates a self-contained virtualenv on first run
    and then starts the local server.
- **Automated releases.** `.github/workflows/release.yml` builds both artifacts
  on a `vX.Y.Z` tag (Windows + Linux runners) and attaches them to the GitHub
  Release. Packaging recipes live under `packaging/windows/` and `packaging/linux/`.

### Changed
- **Update model** now checks HuggingFace *before* downloading: if the local
  model is already up to date it reports so and downloads nothing. It still
  downloads when there is a newer model, or when no model is installed yet, and
  reports a network error (without downloading) if the check can't be made.

### Fixed
- The backend is now OS-agnostic: venv interpreter resolution handles
  `Scripts\python.exe` (Windows) vs `bin/python` (Linux/macOS), and the post-update
  frontend rebuild skips gracefully when Node/pnpm are absent (typical on Linux).

## [2.3.0] - 2026-06-04

### Added
- **Cancel** button while detecting (Text and Files tabs): aborts the request
  (AbortController) and frees the UI immediately so you can pick the correct
  input. `/api/redact-file` also checks for client disconnect after detection
  and skips the redaction step (and download) when cancelled.
- **Single instance**: the portable launcher (`launch.bat` / `start.bat`) checks
  whether a server is already listening on the port and, if so, does NOT start a
  second one — the loading page just reuses the running instance. Prevents
  stacking hidden instances (and reloading the model in RAM) on repeated
  double-clicks.

### Notes
- Closing the browser tab does not stop the server (it runs windowless). Use the
  **Quit** button to stop it. A single in-flight model inference cannot be hard
  -killed mid-pass (CPU/PyTorch), but Cancel returns control immediately and
  skips the remaining work.

## [2.2.2] - 2026-06-04

### Fixed
- Portable launch via `pythonw.exe` (no console) crashed uvicorn silently
  because `sys.stdout`/`sys.stderr` are `None` and uvicorn configures stdout
  stream logging. `server.main.main()` now points those streams at a file when
  missing and runs uvicorn with `log_config=None` (our rotating file handler
  captures uvicorn's logs via propagation), plus logs any startup exception.
  The windowless launcher now actually serves the app.

## [2.2.1] - 2026-06-04

### Changed
- Portable launcher now opens an instant **loading page** (`app/loading.html`)
  on double-click, so the user gets immediate feedback ("Starting…") instead of
  a silent wait. It polls the server and redirects to the app once it responds
  (where the model-download progress is shown); if the server never starts it
  shows recovery steps. Replaces `_open_browser.py`/`error.html`.

## [2.2.0] - 2026-06-04

### Changed
- **Unified into a single project.** The portable builder now lives in this repo
  (`build_portable.ps1`) and packages the repo itself into `portable-build/`
  (git-ignored). The separate `privacy-filter-portable` folder is gone — no more
  duplicated/copied code to keep in sync.
- **Portable-only distribution**: removed the venv-style installer
  (`install.ps1`/`install.bat`/`start.bat`/`uninstall.bat`). Use
  `build_portable.ps1` to produce the app, or the documented dev commands to run
  from source.
- `create_release.py` release notes now point to the portable build.
- README rewritten around the single project (build portable / develop).

### Notes
- The portable build downloads CPU-only PyTorch + the `opf` package +
  `requirements-server.txt` into an embeddable Python, reuses `frontend/dist`
  (builds it with Node only if needed), and writes the windowless launchers.

## [2.1.0] - 2026-06-04

### Added
- **In-app first-run experience**: the model download now runs in the background
  on startup and its progress is shown in the web UI ("Preparing…" screen) via
  an extended `/api/health` (`downloading`, `download_pct`, `error`). No console
  needed.
- **Error/diagnostics layer**: rotating file log at `PF_LOG_DIR/privacy-filter.log`;
  a global exception handler that returns a readable message to the UI; a
  `GET /api/diagnostics` endpoint that returns a `.zip` (version, environment —
  no secrets — and recent logs) plus a **Diagnostics** button in the UI.
- **`POST /api/shutdown`** and a **Quit** button so the app can be stopped from
  the UI (needed when the portable build runs without a console).

### Notes
- These power the portable build's "double-click → opens, no console, errors in
  the web" experience. Defaults unchanged for the normal install.

## [2.0.4] - 2026-06-04

### Fixed
- `server/inference.get_model()` now ensures the checkpoint exists (downloading
  it if missing) **before** constructing `OPF`. When `OPF_CHECKPOINT` is set
  (portable build), `opf` does not auto-download, so an empty model directory
  previously only failed at first redaction with
  `FileNotFoundError: Missing checkpoint config`. The model is now downloaded to
  the configured directory on first use in that case too.

## [2.0.3] - 2026-06-04

### Added
- Environment-configurable runtime so a portable/self-contained build can keep
  everything inside its own folder:
  - `model_update.py` and `server/inference.py` honor `OPF_CHECKPOINT` for the
    model directory (download + load now agree on the same location).
  - `server.main.main()` honors `PF_HOST` / `PF_PORT` (defaults unchanged:
    `0.0.0.0` / `7860`).
  All defaults are unchanged when the variables are unset.

## [2.0.2] - 2026-06-04

### Fixed
- The in-app "Update now" flow now **rebuilds the React frontend** (pnpm) after
  pulling/installing new code, so the served UI always matches the updated
  backend instead of serving a stale bundle.
- Redacted output files that are never downloaded are now removed after a TTL
  (30 min) instead of lingering in TEMP, so no PII output is left on disk.

### Added
- `typecheck` script in the frontend (`tsc --noEmit`).

## [2.0.1] - 2026-06-04

### Changed
- Frontend now uses **pnpm** (via Node's corepack) instead of npm. pnpm uses a
  content-addressed global store with hard links (less disk, faster installs)
  and a strict, non-flat `node_modules`.
- `pnpm-workspace.yaml` allows only `esbuild` to run an install script; pnpm
  blocks all other dependency build scripts by default (hardening against
  malicious postinstall scripts). pnpm version pinned via `packageManager`.
- `install.ps1` builds the frontend with `corepack pnpm install --frozen-lockfile`
  + `corepack pnpm run build`. Replaced `package-lock.json` with `pnpm-lock.yaml`.

## [2.0.0] - 2026-06-03

### Changed
- **Replaced the Gradio web interface with a React (Vite) frontend served by a
  FastAPI backend.** This removes Gradio entirely and with it the Svelte 5
  `effect_update_depth_exceeded` freeze that locked the browser tab on
  tab-switching (present and unfixed through Gradio 6.15.2).
- The app now runs via `uvicorn server.main:app` (port 7860, same as before);
  `start.bat` and `install.ps1` updated accordingly.
- `install.ps1` installs Node.js (LTS) to build the frontend, installs CPU-only
  PyTorch, and installs backend deps from `requirements-server.txt`.

### Added
- `server/` FastAPI backend reusing all existing logic (the `opf` PyTorch model,
  PyMuPDF/python-docx redaction, and the app/model update modules):
  endpoints `/api/redact`, `/api/redact-file` + `/api/download/{token}`,
  `/api/updates(/app|/model)`, `/api/version`, `/api/health`.
- `frontend/` React + TypeScript app (Text / Files / Info tabs, update banners).
- Redacted output files are now deleted after download (no PII left in TEMP).
- Backend tests (`tests/test_server.py`) and `requirements-server.txt`.

### Removed
- `app_local.py` (Gradio UI) and `requirements-web.txt` (Gradio dependency).

## [1.4.0] - 2026-06-03

### Changed
- **Upgraded Gradio from 4.44.0 to 6.15.1** - Major version upgrade that resolves
  all known compatibility issues and security vulnerabilities
- Removed `_patch_gradio_client()` workaround (no longer needed with Gradio 6)
- Simplified `requirements-web.txt` - Gradio 6 manages its own dependencies
  (no more pinning Jinja2, Starlette, or FastAPI separately)
- Updated `install.ps1` to use Gradio 6.15.1

### Fixed
- Resolved `TypeError: unhashable type: 'dict'` Jinja2/Starlette compatibility error
- Fixed tab-switching freeze issues present in Gradio 4.44.x
- Addressed multiple security vulnerabilities in Gradio 4.44.x (CVEs)

### Security
- Updated from Gradio 4.44.0 to 6.15.1, resolving:
  - Arbitrary File Upload vulnerability
  - Allocation of Resources Without Limits
  - Denial of Service vulnerabilities

## [1.3.2] - 2026-06-03

### Fixed
- Pinned `jinja2<3.1` to resolve `TypeError: unhashable type: 'dict'` caused by
  a compatibility issue between Gradio 4.44.0, Starlette, and Jinja2 3.1+ template
  cache. The fix ensures the web interface launches without errors.

### Changed
- Added `requirements-web.txt` with all web interface dependencies for easier
  manual installation
- Updated `install.ps1` to automatically install the Jinja2 fix
- Updated `start.bat` to clarify that the port may vary if 7860 is busy
- Pinned `huggingface_hub<0.25` in `pyproject.toml` for compatibility

## [1.3.1] - 2026-06-02

### Fixed
- Update Now button crashed with `pydantic 2.13 ValidationError: index
  Input should be a valid integer, got a number with a fractional part`
  because the update flow used the deprecated `progress((X, 1.0), desc=...)`
  tuple form. Replaced with `progress(X, desc=...)` (single value) in
  `install_app_update` and `install_model_update`. The 5-step progress in
  `redact_file` is unaffected (it already used integer indices).

## [1.3.0] - 2026-06-02

### Added
- Visible "Click the file above to download it." hint in the Files tab when
  a redacted PDF/DOCX is ready, so the download affordance is obvious
- Detected-entities listing in both the Files tab (table) and the Text tab
  (bullet list) is now wrapped in a `<details>`/`<summary>` collapse block
  so a long redaction result stays compact by default and can be expanded
  on demand
- `app_local.get_model()` now auto-recovers from a partial or missing
  checkpoint by transparently re-downloading the model on next launch
- `install.ps1` accepts `-PythonVersion` and `-GitVersion` parameters so the
  pinned versions can be overridden without editing the script

### Changed
- Refactored `app_local.py` for cleaner code: drop UTF-8 BOM, optimize
  `extract_text_from_pdf` to O(n) with `list + join`, rewrite `redact_docx`
  to preserve run-level formatting across multi-run PII spans, use
  `uuid.uuid4` for unique redacted output filenames, and split the update
  banner logic into focused helpers
- `install.ps1` no longer re-installs dependencies already covered by
  `pyproject.toml` (huggingface_hub, safetensors, tiktoken, fastapi,
  starlette, jinja2, pydantic, pydantic_core) and switches gradio /
  gradio_client to a pinned exact version to avoid a known tab-switching
  freeze in 4.45+ and 5.x

### Fixed
- Model update flow is now atomic: the new checkpoint is downloaded to a
  temporary directory and only swapped into place after it validates, so a
  network blip or app close mid-update can no longer leave the user with
  a broken `~/.opf/privacy_filter`
- `gr.update` callbacks for the update banners no longer swallow exceptions
  silently; failures are logged to the console

### Removed
- Unused `privacy-filter/opf/_common/update_check.py` (no references in the
  package)

## [1.2.3] - 2026-06-02

### Fixed
- Fixed project_dir path in download_and_install_update function

## [1.2.2] - 2026-06-02

### Fixed
- Fixed restart mechanism to properly detach new process
- App now tries next port if 7860 is already in use (up to 7870)
- Uses venv Python for restart when available

## [1.2.1] - 2026-06-02

### Fixed
- Fixed VERSION file path in app_update.py (was pointing to wrong directory)
- App updates now use `git pull` when no ZIP asset is attached to release
- Improved error messages for update failures

## [1.2.0] - 2026-06-02

### Added
- Virtual environment support for clean dependency isolation
- `uninstall.bat` for easy removal
- `.gitignore` to exclude sensitive files from version control
- Better error handling for PDF extraction
- Text file encoding fallback for non-UTF-8 files

### Changed
- Installer now clones from `franbetalegal/PrivacyFilterLocal` (not `openai/privacy-filter`)
- Dependencies are installed in `.venv/` instead of globally
- `start.bat` uses virtual environment Python
- App updates now include `app_local.py` (removed from preserve list)
- `create_release.py` reads version from `VERSION` file automatically

### Fixed
- Removed duplicate `update_model()` function (use `install_model_update()` instead)
- Removed unused `Pt` import from `docx.shared`
- Fixed `except: pass` in PDF extraction to log errors
- Fixed `model_update.py` sys.path manipulation (moved to module level)
- Fixed `create_release.py` to read from `.env` without insecure git credential fallback
- Fixed thread safety for model update state

### Removed
- `install.ps1` no longer generates `app_local.py` (uses repo version)
- Removed Spanish language examples from generated code

## [1.0.0] - 2026-06-02

### Added
- Initial release of Privacy Filter Local
- PII detection and redaction for text, PDF, and DOCX files
- 8 PII categories: PERSON, EMAIL, PHONE, ADDRESS, DATE, URL, ACCOUNT_NUMBER, SECRET
- Web interface built with Gradio
- Automatic model update checking from HuggingFace
- Automatic app update checking from GitHub Releases
- One-click update installation
- Changelog display in update banner
- Full English UI translation
- Windows installer script (install.ps1)
- Command-line interface (opf command)

### Features
- 100% local processing - no data leaves your computer
- Real-time PII detection with progress indicators
- PDF redaction with proper formatting
- DOCX redaction with font preservation
- Batch file processing support
- Interactive CLI mode
- Model caching for faster subsequent runs

### Security
- No telemetry or data collection
- Local model inference only
- Apache 2.0 license
- Dependencies from trusted sources only
