# Changelog

All notable changes to Privacy Filter Local will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
pip install -e .
```

### Creating a New Release
```bash
# Update VERSION file and create GitHub Release
python create_release.py 1.2.0 --changelog-file CHANGELOG.md

# Or with inline changelog
python create_release.py 1.2.0 --changelog "## What's New\n• Feature X\n• Feature Y"
```
