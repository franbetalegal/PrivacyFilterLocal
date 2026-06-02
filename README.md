# Privacy Filter - Local

100% local PII detection and redaction tool powered by OpenAI's Privacy Filter model.

## Features

- Detects 8 types of PII: names, emails, phones, addresses, dates, URLs, account numbers, and secrets
- Processes text, PDF, and DOCX files
- Returns redacted PDF/DOCX with PII masked
- Runs entirely offline - no data leaves your computer
- Automatic update checking via HuggingFace API
- Web interface built with Gradio

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
3. Clone the repository
4. Install all dependencies
5. Launch the web interface

### Option 2: Manual installation

```bash
# Clone the repository
git clone https://github.com/openai/privacy-filter.git
cd privacy-filter

# Install the package
pip install -e .

# Install web interface dependencies
pip install gradio==4.44.0 gradio_client==1.3.0 PyMuPDF python-docx

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
privacy-filter/
├── app_local.py          # Web interface
├── start.bat             # Launch script
├── install.bat           # Installer launcher
├── install.ps1           # Full installer
└── privacy-filter/       # Core OPF package
    ├── opf/              # Main package
    ├── pyproject.toml    # Package config
    └── examples/         # Demo data and scripts
```

## Automatic Update Checking

The application automatically checks for model updates when launched. If an update is available, a banner will appear at the top of the interface with an "Update model now" button.

## License

Apache 2.0 - See [LICENSE](privacy-filter/LICENSE) for details.

## Credits

Based on [OpenAI Privacy Filter](https://github.com/openai/privacy-filter).
