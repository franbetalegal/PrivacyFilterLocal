# Changelog

All notable changes to Privacy Filter Local will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

---

## How to Update

### Automatic Update (Recommended)
1. When a new version is available, a banner will appear at the top of the web interface
2. Click "Update now" to download and install the update
3. The app will restart automatically

### Manual Update
```bash
cd C:\privacy-filter
git pull
.venv\Scripts\pip.exe install -e .\privacy-filter
```

### Creating a New Release
```bash
# Set your GitHub token
$env:GITHUB_TOKEN = "your-token-here"

# Create release (reads VERSION and CHANGELOG.md automatically)
python create_release.py
```
