# Privacy Filter - Local

100% local PII detection and redaction tool powered by OpenAI's Privacy Filter model.

## Features

- Detects 8 types of PII: names, emails, phones, addresses, dates, URLs, account numbers, and secrets
- Processes text, PDF, and DOCX files
- Returns redacted PDF/DOCX with PII masked
- Runs entirely offline - no data leaves your computer
- Automatic model update checking via HuggingFace API
- Automatic app update checking via GitHub Releases
- One-click update installation with changelog display
- Web interface built with Gradio 6

## Requirements

- Windows 10/11
- Python 3.10+
- Git
- Internet connection (only for initial download and update checks)

## Quick Install

### Option 1: Run the installer

```bash
install.bat
```

This will automatically:
1. Install Python 3.12 (if not present)
2. Install Git (if not present)
3. Clone the repository to `C:\privacy-filter`
4. Create a Python virtual environment
5. Install all dependencies
6. Launch the web interface

### Option 2: Manual installation

```bash
# Clone the repository
git clone https://github.com/franbetalegal/PrivacyFilterLocal.git C:\privacy-filter
cd C:\privacy-filter

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install the package
cd privacy-filter
pip install -e .
cd ..

# Install web interface dependencies
pip install -r requirements-web.txt

# Run the application
python app_local.py
```

## Usage

1. Open your browser and go to `http://localhost:7860`
2. Use the **Text** tab to analyze text directly
3. Use the **Files** tab to upload PDF or DOCX files
4. The **Info** tab shows supported PII categories

### Command Line

```bash
# Redact text directly
opf redact "My name is John, email: john@example.com"

# Process a file
opf redact --text-file document.txt

# Interactive mode
opf redact
```

## Project Structure

```
C:\privacy-filter\
├── .venv/                  # Python virtual environment
├── app_local.py            # Web interface
├── app_update.py           # Auto-update module (app)
├── model_update.py         # Auto-update module (model)
├── create_release.py       # Release creation script
├── start.bat               # Launch script
├── install.bat             # Installer launcher
├── install.ps1             # Full installer
├── uninstall.bat           # Uninstaller
├── VERSION                 # Current version number
├── CHANGELOG.md            # Version history
└── privacy-filter/         # Core OPF package
    ├── opf/                # Main package
    ├── pyproject.toml      # Package config
    └── examples/           # Demo data and scripts
```

## Updating

### Automatic Update (Recommended)

The application automatically checks for updates when launched. If an update is available:

1. A banner appears at the top of the interface showing:
   - Current version and new version
   - Changelog with new features and fixes
2. Click **"Update now"** to download and install
3. The app restarts automatically with the new version

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

## Version History

See [CHANGELOG.md](CHANGELOG.md) for a complete list of changes.

## Automatic Update Checking

The application checks for updates from two sources:

1. **Model Updates** (HuggingFace): Checks for newer model versions
2. **App Updates** (GitHub Releases): Checks for newer application versions with changelog

Both checks are silent and only show notifications when updates are available.

## Uninstall

Run `uninstall.bat` to remove the application. Optionally remove the cached model from `~\.opf\privacy_filter`.

## License

Apache 2.0 - See [LICENSE](privacy-filter/LICENSE) for details.

## Credits

Based on [OpenAI Privacy Filter](https://github.com/openai/privacy-filter).
