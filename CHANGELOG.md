# Changelog

All notable changes to Privacy Filter Local will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
