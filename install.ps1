#Requires -Version 5.1
<#
.SYNOPSIS
    Complete installer for OpenAI Privacy Filter with local web interface.
.DESCRIPTION
    Checks and installs all required dependencies with multiple fallbacks.
.PARAMETER Force
    Force reinstallation/overwrite of existing files.
.PARAMETER NoRun
    Install only, do not launch the application.
.EXAMPLE
    .\install.ps1
    .\install.ps1 -Force
#>

param(
    [switch]$Force,
    [switch]$NoRun
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# ============================================================
#  CONFIGURATION
# ============================================================

$PROJECT_DIR = "C:\privacy-filter"
$REPO_DIR = "$PROJECT_DIR\privacy-filter"
$REPO_URL = "https://github.com/openai/privacy-filter.git"
$PYTHON_URL = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
$PYTHON_INSTALLER = "$env:TEMP\python-installer-312.exe"

# ============================================================
#  LOG FUNCTIONS
# ============================================================

function Write-Step { param([string]$M); Write-Host "`n=== $M ===" -ForegroundColor Cyan }
function Write-OK   { param([string]$M); Write-Host "  [OK] $M" -ForegroundColor Green }
function Write-Warn { param([string]$M); Write-Host "  [!] $M" -ForegroundColor Yellow }
function Write-Fail { param([string]$M); Write-Host "  [X] $M" -ForegroundColor Red }
function Write-Info { param([string]$M); Write-Host "  [*] $M" -ForegroundColor Gray }

function Test-CommandExists {
    param([string]$Cmd)
    $null -ne (Get-Command $Cmd -ErrorAction SilentlyContinue)
}

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH = "$machine;$user"
}

# ============================================================
#  PYTHON DETECTION
# ============================================================

function Test-PythonReal {
    <#
    .SYNOPSIS
        Finds a real Python 3.10+ (not a Windows Store stub).
    #>
    # Search PATH commands
    foreach ($cmd in @("python", "python3", "python3.12", "python3.11", "python3.10")) {
        try {
            $output = & $cmd --version 2>&1 | Out-String
            if ($output -match "Python 3\.(\d+)") {
                $minor = [int]$Matches[1]
                if ($minor -ge 10) {
                    $full = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
                    if ($full -and $full -notmatch "WindowsApps") {
                        return @{ Found=$true; Path=$full; Version=$output.Trim() }
                    }
                }
            }
        } catch { }
    }

    # Search known paths
    foreach ($p in @(
        "C:\Python312\python.exe", "C:\Python311\python.exe", "C:\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe",
        "C:\Program Files\Python310\python.exe"
    )) {
        if (Test-Path $p) {
            try {
                $output = & $p --version 2>&1 | Out-String
                if ($output -match "Python 3\.(\d+)") {
                    $minor = [int]$Matches[1]
                    if ($minor -ge 10) {
                        return @{ Found=$true; Path=$p; Version=$output.Trim() }
                    }
                }
            } catch { }
        }
    }

    return @{ Found=$false }
}

# ============================================================
#  PYTHON INSTALLATION (3 methods with fallback)
# ============================================================

function Install-Python {
    Write-Step "INSTALLING PYTHON"

    # Method 1: winget
    if (Test-CommandExists "winget") {
        Write-Info "Trying winget..."
        $null = winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent 2>&1
        Refresh-Path
        $check = Test-PythonReal
        if ($check.Found) { Write-OK "Python installed via winget: $($check.Version)"; return $true }
        Write-Warn "winget failed"
    }

    # Method 2: chocolatey
    if (Test-CommandExists "choco") {
        Write-Info "Trying chocolatey..."
        $null = choco install python -y 2>&1
        Refresh-Path
        $check = Test-PythonReal
        if ($check.Found) { Write-OK "Python installed via choco: $($check.Version)"; return $true }
        Write-Warn "chocolatey failed"
    }

    # Method 3: Direct download
    Write-Info "Downloading Python from python.org..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $PYTHON_URL -OutFile $PYTHON_INSTALLER -UseBasicParsing -TimeoutSec 300
        Write-OK "Download complete"

        Write-Info "Installing silently (may take 1-2 minutes)..."
        $proc = Start-Process -FilePath $PYTHON_INSTALLER -ArgumentList @(
            "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_pip=1",
            "Include_test=0", "TargetDir=C:\Python312", "CompileAll=0"
        ) -Wait -PassThru -NoNewWindow

        Remove-Item $PYTHON_INSTALLER -Force -ErrorAction SilentlyContinue

        if ($proc.ExitCode -eq 0 -or $proc.ExitCode -eq 3010) {
            Refresh-Path
            $check = Test-PythonReal
            if ($check.Found) { Write-OK "Python installed via download: $($check.Version)"; return $true }
        }
        Write-Fail "Installer exited with code $($proc.ExitCode)"
    } catch {
        Write-Fail "Error: $_"
        Remove-Item $PYTHON_INSTALLER -Force -ErrorAction SilentlyContinue
    }

    return $false
}

# ============================================================
#  GIT INSTALLATION (3 methods with fallback)
# ============================================================

function Install-Git {
    Write-Step "INSTALLING GIT"

    # Method 1: winget
    if (Test-CommandExists "winget") {
        Write-Info "Trying winget..."
        $null = winget install Git.Git --accept-source-agreements --accept-package-agreements --silent 2>&1
        Refresh-Path
        if (Test-CommandExists "git") { Write-OK "Git installed via winget"; return $true }
        Write-Warn "winget failed"
    }

    # Method 2: chocolatey
    if (Test-CommandExists "choco") {
        Write-Info "Trying chocolatey..."
        $null = choco install git -y 2>&1
        Refresh-Path
        if (Test-CommandExists "git") { Write-OK "Git installed via choco"; return $true }
        Write-Warn "chocolatey failed"
    }

    # Method 3: Direct download
    Write-Info "Downloading Git..."
    $gitUrl = "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.2/Git-2.47.1.2-64-bit.exe"
    $gitInstaller = "$env:TEMP\git-installer.exe"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $gitUrl -OutFile $gitInstaller -UseBasicParsing -TimeoutSec 300
        Write-OK "Download complete"

        Write-Info "Installing silently..."
        $proc = Start-Process -FilePath $gitInstaller -ArgumentList @(
            "/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-",
            "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS",
            "/COMPONENTS=icons,ext,ext\shellhere,ext\guihere,gitlfs,assoc,assoc_sh"
        ) -Wait -PassThru -NoNewWindow

        Remove-Item $gitInstaller -Force -ErrorAction SilentlyContinue

        if ($proc.ExitCode -eq 0 -or $proc.ExitCode -eq 3010) {
            Refresh-Path
            if (Test-CommandExists "git") { Write-OK "Git installed via download"; return $true }
        }
        Write-Fail "Installer exited with code $($proc.ExitCode)"
    } catch {
        Write-Fail "Error: $_"
        Remove-Item $gitInstaller -Force -ErrorAction SilentlyContinue
    }

    return $false
}

# ============================================================
#  CLONE REPOSITORY (with full verification)
# ============================================================

function Clone-Repository {
    Write-Step "CLONING REPOSITORY"

    # Check connectivity
    Write-Info "Checking connectivity to GitHub..."
    try {
        $response = Invoke-WebRequest -Uri "https://github.com" -UseBasicParsing -TimeoutSec 10 -Method Head
        Write-OK "Connectivity OK"
    } catch {
        Write-Fail "Could not connect to GitHub. Check your internet connection."
        return $false
    }

    # Check if repository already exists and is complete
    if (Test-Path "$REPO_DIR\.git") {
        Write-Warn "Repository already exists"

        # Check if complete
        $hasOpf = Test-Path "$REPO_DIR\opf"
        $hasReadme = Test-Path "$REPO_DIR\README.md"
        $hasPyproject = Test-Path "$REPO_DIR\pyproject.toml"

        if ($hasOpf -and $hasReadme -and $hasPyproject) {
            Write-OK "Repository verified (complete)"
            if (-not $Force) {
                return $true
            }
            Write-Warn "Forcing re-clone..."
        } else {
            Write-Warn "Repository incomplete. Removing..."
        }

        Remove-Item -Recurse -Force $REPO_DIR -ErrorAction SilentlyContinue
    }

    # Remove empty directory if it exists
    if (Test-Path $REPO_DIR) {
        Remove-Item -Recurse -Force $REPO_DIR -ErrorAction SilentlyContinue
    }

    # Clone
    Write-Info "Cloning from $REPO_URL..."
    $gitOutput = & git clone $REPO_URL $REPO_DIR 2>&1 | Out-String

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Error during git clone:"
        Write-Host $gitOutput -ForegroundColor Red
        return $false
    }

    # Verify clone was successful
    $checks = @{
        ".git"     = Test-Path "$REPO_DIR\.git"
        "opf"      = Test-Path "$REPO_DIR\opf"
        "README"   = Test-Path "$REPO_DIR\README.md"
        "pyproject"= Test-Path "$REPO_DIR\pyproject.toml"
    }

    $allOk = $true
    foreach ($key in $checks.Keys) {
        if ($checks[$key]) {
            Write-OK "$key verified"
        } else {
            Write-Fail "$key NOT found"
            $allOk = $false
        }
    }

    if ($allOk) {
        Write-OK "Repository cloned and verified successfully"
        return $true
    } else {
        Write-Fail "Repository is incomplete"
        return $false
    }
}

# ============================================================
#  INSTALL PYTHON DEPENDENCIES
# ============================================================

function Install-Dependencies {
    Write-Step "INSTALLING PYTHON DEPENDENCIES"

    $py = Test-PythonReal
    if (-not $py.Found) {
        Write-Fail "Python not found"
        return $false
    }

    $pythonPath = $py.Path
    $pythonDir = Split-Path $pythonPath
    $pipPath = Join-Path $pythonDir "pip.exe"

    # If pip.exe doesn't exist, try pip3.exe
    if (-not (Test-Path $pipPath)) {
        $pipPath = Join-Path $pythonDir "pip3.exe"
    }

    # If still not found, use python -m pip
    $useModule = -not (Test-Path $pipPath)
    if ($useModule) {
        Write-Warn "pip.exe not found, using python -m pip"
        $pipPath = $pythonPath
    }

    # Update pip
    Write-Info "Updating pip..."
    if ($useModule) {
        & $pythonPath -m pip install --upgrade pip 2>&1 | Out-Null
    } else {
        & $pipPath install --upgrade pip 2>&1 | Out-Null
    }
    Write-OK "pip updated"

    # Install project dependencies
    Write-Info "Installing project dependencies..."
    Push-Location $REPO_DIR

    if ($useModule) {
        $output = & $pythonPath -m pip install -e . 2>&1 | Out-String
    } else {
        $output = & $pipPath install -e . 2>&1 | Out-String
    }

    if ($LASTEXITCODE -eq 0) {
        Write-OK "Project dependencies installed"
    } else {
        Write-Warn "Some dependencies may have failed"
        Write-Info $output
    }

    # Web interface dependencies
    Write-Info "Installing web interface dependencies..."
    $webDeps = @(
        "gradio==4.44.0",
        "gradio_client==1.3.0",
        "huggingface_hub==0.24.0",
        "fastapi==0.109.2",
        "starlette==0.36.3",
        "jinja2==3.1.6",
        "pydantic==2.13.4",
        "pydantic_core==2.46.4",
        "safetensors",
        "tiktoken",
        "PyMuPDF",
        "python-docx"
    )
    foreach ($dep in $webDeps) {
        if ($useModule) {
            & $pythonPath -m pip install --force-reinstall $dep 2>&1 | Out-Null
        } else {
            & $pipPath install --force-reinstall $dep 2>&1 | Out-Null
        }
        if ($LASTEXITCODE -eq 0) {
            Write-OK "$dep installed"
        } else {
            Write-Warn "Error installing $dep"
        }
    }

    Pop-Location
    return $true
}

# ============================================================
#  CREATE APP_LOCAL.PY
# ============================================================

function New-AppLocal {
    Write-Step "GENERATING APP_LOCAL.PY"

    $appPath = "$PROJECT_DIR\app_local.py"

    if ((Test-Path $appPath) -and -not $Force) {
        Write-Warn "app_local.py already exists (use -Force to overwrite)"
        return
    }

    $appContent = @'
import sys
import time
import threading
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
REPO_DIR = PROJECT_DIR / "privacy-filter"
sys.path.insert(0, str(REPO_DIR))

def _patch_gradio_client():
    try:
        from gradio_client import utils as _cu
        _orig_get_type = _cu.get_type
        if getattr(_orig_get_type, "_patched", False):
            return
        def _safe_get_type(schema):
            if not isinstance(schema, dict):
                return "Any"
            return _orig_get_type(schema)
        _safe_get_type._patched = True
        _cu.get_type = _safe_get_type
        _orig_json = _cu._json_schema_to_python_type
        def _safe_json(schema, defs):
            if not isinstance(schema, dict):
                return "Any"
            return _orig_json(schema, defs)
        _cu._json_schema_to_python_type = _safe_json
    except Exception:
        pass

_patch_gradio_client()

import gradio as gr

_model = None

def get_model():
    global _model
    if _model is not None:
        return _model
    print("Loading Privacy Filter model...")
    try:
        from opf._api import OPF
        _model = OPF(device="cpu")
        print("[OK] Model loaded")
        return _model
    except Exception as e:
        print(f"[ERROR] {e}")
        raise

def extract_text_from_pdf(pdf_path):
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text
    except:
        pass
    return None

def extract_text_from_docx(docx_path):
    try:
        from docx import Document
        doc = Document(docx_path)
        text = "\n".join(p.text for p in doc.paragraphs)
        return text
    except Exception as e:
        print(f"[DOCX ERROR] {e}")
    return None

def redact_pdf(input_path, detected_spans):
    import fitz
    import os
    import time
    out_path = os.path.join(os.environ.get("TEMP", os.path.dirname(input_path)), f"redacted_{int(time.time()*1000)}.pdf")
    doc = fitz.open(input_path)
    for page in doc:
        for span in detected_spans:
            old_text = span.text
            new_text = span.placeholder
            if not old_text or old_text == new_text:
                continue
            results = page.search_for(old_text)
            for rect in results:
                page.add_redact_annot(
                    rect, text=new_text, fontsize=9,
                    fontname="helv", fill=(1, 1, 1), text_color=(0, 0, 0),
                    align=0
                )
        page.apply_redactions()
    doc.save(out_path)
    doc.close()
    return out_path

def redact_docx(input_path, detected_spans):
    import os
    import time
    from docx import Document
    from docx.shared import Pt
    out_path = os.path.join(os.environ.get("TEMP", os.path.dirname(input_path)), f"redacted_{int(time.time()*1000)}.docx")
    doc = Document(input_path)
    for span in detected_spans:
        old_text = span.text
        new_text = span.placeholder
        if not old_text or old_text == new_text:
            continue
        for para in doc.paragraphs:
            if old_text not in para.text:
                continue
            full = para.text
            new_full = full.replace(old_text, new_text)
            if full == new_full:
                continue
            if len(para.runs) == 0:
                continue
            fmt = para.runs[0].font
            font_name = fmt.name
            font_size = fmt.size
            font_bold = fmt.bold
            font_italic = fmt.italic
            for run in para.runs:
                run.text = ""
            para.runs[0].text = new_full
            if font_name:
                para.runs[0].font.name = font_name
            if font_size:
                para.runs[0].font.size = font_size
            if font_bold is not None:
                para.runs[0].font.bold = font_bold
            if font_italic is not None:
                para.runs[0].font.italic = font_italic
    doc.save(out_path)
    return out_path

def redact_text(text):
    if not text or not text.strip():
        return "", "Enter some text."
    try:
        model = get_model()
        if model is None:
            return text, "Loading model..."
        start = time.time()
        result = model.redact(text)
        elapsed = time.time() - start
        redacted = result.redacted_text if hasattr(result, 'redacted_text') else str(result)
        spans = result.detected_spans if hasattr(result, 'detected_spans') else []
        if spans:
            summary = f"**{len(spans)} entities detected** ({elapsed:.1f}s)\n\n"
            for s in spans:
                label = s.label if hasattr(s, 'label') else "?"
                txt = s.text if hasattr(s, 'text') else ""
                summary += f"- `{label}`: {txt}\n"
        else:
            summary = f"_No PII entities detected_ ({elapsed:.1f}s)"
        return redacted, summary
    except Exception as e:
        return text, f"Error: {e}"

def redact_file(file, progress=gr.Progress()):
    if file is None:
        return "_Upload a file._", None
    try:
        progress((0, 5), desc="Loading model...")
        model = get_model()
        if model is None:
            return "_Loading model..._", None

        progress((1, 5), desc="Reading file...")
        path = Path(file.name)
        ext = path.suffix.lower()
        if ext == ".pdf":
            text = extract_text_from_pdf(str(path))
            if text is None:
                return "**Error** reading PDF. Install: `pip install PyMuPDF`", None
        elif ext == ".docx":
            text = extract_text_from_docx(str(path))
            if text is None:
                return "**Error** reading DOCX. Check the console.", None
        else:
            text = path.read_text(encoding="utf-8")

        progress((2, 5), desc="Detecting PII...")
        start = time.time()
        result = model.redact(text)
        elapsed = time.time() - start
        redacted = result.redacted_text if hasattr(result, 'redacted_text') else str(result)
        spans = result.detected_spans if hasattr(result, 'detected_spans') else []

        progress((3, 5), desc="Generating redacted output...")
        legend = f"### Redaction Result\n\n"
        legend += f"Processed in **{elapsed:.1f}s** -- **{len(spans)}** entities detected\n\n"
        if spans:
            legend += "| # | Type | Original | Replacement |\n"
            legend += "|--:|------|----------|----------|\n"
            for i, s in enumerate(spans, 1):
                label = s.label if hasattr(s, 'label') else "?"
                txt = s.text if hasattr(s, 'text') else ""
                ph = s.placeholder if hasattr(s, 'placeholder') else ""
                legend += f"| {i} | `{label}` | {txt} | {ph} |\n"
        else:
            legend += "_No PII entities detected._"

        progress((4, 5), desc="Creating output file...")
        if ext == ".pdf" and spans:
            pdf_path = redact_pdf(str(path), spans)
            progress((5, 5), desc="Done")
            return legend, pdf_path
        if ext == ".docx" and spans:
            docx_path = redact_docx(str(path), spans)
            progress((5, 5), desc="Done")
            return legend, docx_path
        progress((5, 5), desc="Done")
        return legend, None
    except Exception as e:
        return f"**Error:** {e}", None

def update_model(progress=gr.Progress()):
    global _model
    try:
        import shutil
        from pathlib import Path as _P
        model_dir = _P.home() / ".opf" / "privacy_filter"
        if not model_dir.exists():
            progress((1, 3), desc="No cached model, will download on first use.")
            return "_No local model found. It will be downloaded automatically the first time you use Detect._"
        progress((1, 3), desc="Removing current model...")
        shutil.rmtree(str(model_dir))
        progress((2, 3), desc="Downloading updated model...")
        _model = None
        get_model()
        progress((3, 3), desc="Done")
        return "_Model updated successfully._"
    except Exception as e:
        return f"**Error** updating: {e}"


def _check_update_background(update_banner, update_btn):
    """Background thread that checks for model updates."""
    try:
        from opf._common.update_check import check_for_update
        info = check_for_update()
        if info.error:
            return
        if info.update_available:
            date_str = f" ({info.remote_date[:10]})" if info.remote_date else ""
            update_banner.update(
                value=f"### A model update is available{date_str}\n"
                      f"Current: `{info.local_hash[:8] if info.local_hash else 'unknown'}` | "
                      f"Latest: `{info.remote_hash[:8] if info.remote_hash else '?'}`",
                visible=True,
            )
            update_btn.update(visible=True)
    except Exception:
        pass


def create_ui():
    with gr.Blocks(title="Privacy Filter - Local") as app:
        update_banner = gr.Markdown(
            value="",
            visible=False,
        )
        update_btn = gr.Button(
            "Update model now",
            variant="primary",
            visible=False,
        )
        update_msg = gr.Markdown()
        update_btn.click(fn=update_model, outputs=update_msg)

        gr.Markdown("# Privacy Filter - Local\n*100% local PII detection*")

        with gr.Tab("Text"):
            with gr.Row():
                inp = gr.Textbox(
                    label="Text to analyze",
                    lines=5,
                    placeholder="My name is John, email: john@example.com, phone: +1 555 123 4567"
                )
                out = gr.Textbox(label="Redacted output", lines=5)
            btn = gr.Button("Detect PII", variant="primary")
            info = gr.Markdown("_Enter text and click Detect._")
            btn.click(fn=redact_text, inputs=inp, outputs=[out, info])
            gr.Examples(
                examples=[
                    "Hola, soy Maria Lopez. Mi email es maria@empresa.com y mi DNI es 12345678Z.",
                    "Contacta al +34 912 345 678 o envia email a ayuda@soporte.es",
                    "La reunion es el 15/03/2026. Cuenta: ES91 2100 0418 4502 0005 1332",
                ],
                inputs=inp
            )

        with gr.Tab("Files"):
            gr.Markdown("Upload text or PDF files to redact PII.")
            finp = gr.File(
                label="Upload file",
                file_types=[".txt",".md",".csv",".json",".log",".py",".js",".xml",".html",".pdf",".docx"]
            )
            fbtn = gr.Button("Process File", variant="primary")
            flegend = gr.Markdown()
            fpdf = gr.File(label="Redacted file (PDF/DOCX)", visible=True)
            fbtn.click(fn=redact_file, inputs=finp, outputs=[flegend, fpdf])

        with gr.Tab("Info"):
            gr.Markdown("""
            ## PII Categories
            
            | Category | Description |
            |----------|-------------|
            | PERSON | Person names |
            | EMAIL | Email addresses |
            | PHONE | Phone numbers |
            | ADDRESS | Postal addresses |
            | DATE | Personal dates |
            | URL | Web links |
            | ACCOUNT_NUMBER | Bank accounts, cards |
            | SECRET | Passwords, API keys |
            
            ## Supported Formats
            
            - Text: .txt, .md, .csv, .json, .log, .py, .js, .xml, .html
            - PDF: .pdf (returns redacted PDF)
            - DOCX: .docx (returns redacted DOCX)
            
            ## Security
            
            - 100% local - nothing is sent to the internet
            - Model runs on your PC
            - Apache 2.0 license
            """)
            manual_update_btn = gr.Button("Update model", variant="secondary")
            manual_update_msg = gr.Markdown()
            manual_update_btn.click(fn=update_model, outputs=manual_update_msg)

        app.load(
            fn=lambda: None,
            inputs=None,
            outputs=None,
            js="""() => {
                setTimeout(() => {
                    const banner = document.querySelector('[data-testid="markdown"]');
                    if (banner) banner.scrollIntoView({behavior: 'smooth'});
                }, 3000);
            }""",
        )

    return app


def _start_update_check(app):
    """Start background update check after a short delay."""
    def _delayed_check():
        time.sleep(5)
        try:
            from opf._common.update_check import check_for_update
            info = check_for_update()
            if info.update_available:
                date_str = f" ({info.remote_date[:10]})" if info.remote_date else ""
                banner_text = (
                    f"### A model update is available{date_str}\n"
                    f"Current: `{info.local_hash[:8] if info.local_hash else 'unknown'}` | "
                    f"Latest: `{info.remote_hash[:8] if info.remote_hash else '?'}`"
                )
                print(f"[UPDATE] Model update available: {info.remote_hash[:8] if info.remote_hash else '?'}")
        except Exception:
            pass
    threading.Thread(target=_delayed_check, daemon=True).start()


if __name__ == "__main__":
    print("=" * 50)
    print("  Privacy Filter - Local Interface")
    print("=" * 50)
    print()
    print("The model will be loaded the first time you use Detect.")
    print()
    print("Open http://localhost:7860")
    print()

    app = create_ui()
    _start_update_check(app)
    app.queue()
    app.launch(server_name="0.0.0.0", server_port=7860, share=False)
'@

    Set-Content -Path $appPath -Value $appContent -Encoding UTF8
    Write-OK "app_local.py created"

    # Create start.bat
    $batPath = "$PROJECT_DIR\start.bat"
    $batContent = @"
@echo off
title Privacy Filter - Local
color 0A

echo ========================================
echo   Privacy Filter - Local
echo ========================================
echo.

cd /d "%~dp0"

echo Starting web server...
echo Open http://localhost:7860 in your browser
echo Press Ctrl+C to stop
echo.

python app_local.py

pause
"@
    Set-Content -Path $batPath -Value $batContent -Encoding ASCII
    Write-OK "start.bat created"
}

# ============================================================
#  MAIN
# ============================================================

function Main {
    Clear-Host

    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host "  OpenAI Privacy Filter - Complete Installer" -ForegroundColor Cyan
    Write-Host "================================================================" -ForegroundColor Cyan

    $startTime = Get-Date

    # PHASE 0: Initial diagnostics
    Write-Step "PHASE 0: SYSTEM DIAGNOSTICS"

    # Check Python
    $py = Test-PythonReal
    if ($py.Found) {
        Write-OK "Python found: $($py.Version)"
    } else {
        Write-Warn "Python NOT found"
    }

    # Check Git
    if (Test-CommandExists "git") {
        Write-OK "Git found: $(git --version)"
    } else {
        Write-Warn "Git NOT found"
    }

    # Check package manager
    if (Test-CommandExists "winget") {
        Write-OK "winget available"
    } elseif (Test-CommandExists "choco") {
        Write-OK "chocolatey available"
    } else {
        Write-Warn "No package manager (will use direct download)"
    }

    # PHASE 1: Python
    if (-not $py.Found) {
        if (-not (Install-Python)) {
            Write-Host "`n[FATAL] Could not install Python." -ForegroundColor Red
            Write-Host "Install manually: https://www.python.org/downloads/" -ForegroundColor Yellow
            Write-Host "Check 'Add Python to PATH'" -ForegroundColor Yellow
            exit 1
        }
    }

    # PHASE 2: Git
    if (-not (Test-CommandExists "git")) {
        if (-not (Install-Git)) {
            Write-Host "`n[FATAL] Could not install Git." -ForegroundColor Red
            Write-Host "Install manually: https://git-scm.com/download/win" -ForegroundColor Yellow
            exit 1
        }
    }

    # PHASE 3: Repository
    if (-not (Clone-Repository)) {
        Write-Host "`n[FATAL] Could not obtain repository." -ForegroundColor Red
        exit 1
    }

    # PHASE 4: Dependencies
    if (-not (Install-Dependencies)) {
        Write-Host "`n[FATAL] Could not install dependencies." -ForegroundColor Red
        exit 1
    }

    # PHASE 5: App
    New-AppLocal

    $elapsed = (Get-Date) - $startTime
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "  INSTALLATION COMPLETE ($($elapsed.Minutes)m $($elapsed.Seconds)s)" -ForegroundColor Green
    Write-Host "================================================================" -ForegroundColor Green

    # PHASE 6: Run
    if (-not $NoRun) {
        Write-Host ""
        Write-Host "  Open http://localhost:7860 in your browser" -ForegroundColor Cyan
        Write-Host "  Press Ctrl+C to stop" -ForegroundColor Yellow
        Write-Host ""

        Push-Location $PROJECT_DIR
        python app_local.py
        Pop-Location
    } else {
        Write-Host ""
        Write-Host "To run:" -ForegroundColor Cyan
        Write-Host "  cd $PROJECT_DIR" -ForegroundColor White
        Write-Host "  python app_local.py" -ForegroundColor White
        Write-Host ""
    }
}

Main
