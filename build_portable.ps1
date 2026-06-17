#Requires -Version 5.1
<#
.SYNOPSIS
    Builds a portable, self-contained Privacy Filter package from THIS repo.
.DESCRIPTION
    Packages the current project (server/ + frontend/ + opf) into a fully
    self-contained folder under .\portable-build that runs on any Windows
    machine without installing anything on the host. It downloads an embeddable
    Python, installs the backend dependencies + the OPF model package, copies the
    FastAPI backend and the built React frontend, and writes the launchers.

    At runtime the launcher keeps the model, HuggingFace/tiktoken caches, temp
    files and logs INSIDE the package folder (nothing in ~/.opf, %APPDATA% or
    ~/.cache). Delete the folder and no trace remains. The end user never needs
    Python or Node.
.PARAMETER PythonVersion
    Python version to embed (default: 3.12.10).
.PARAMETER Force
    Re-download the embeddable Python runtime.
.PARAMETER SkipDeps
    Skip dependency installation (re-copy app code / frontend only).
.PARAMETER IncludeModel
    Pre-download the PII model so the package works offline from the first run
    (adds ~2.7 GB). Off by default (downloaded on first run, shown in the app).
.PARAMETER RebuildFrontend
    Force a fresh React build with Node/pnpm before packaging (use after UI
    changes). Otherwise an existing frontend\dist is reused.
.EXAMPLE
    .\build_portable.ps1
    .\build_portable.ps1 -IncludeModel
    .\build_portable.ps1 -RebuildFrontend
#>

param(
    [string]$PythonVersion = "3.12.10",
    [switch]$Force,
    [switch]$SkipDeps,
    [switch]$IncludeModel,
    [switch]$RebuildFrontend
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ============================================================
#  CONFIGURATION
# ============================================================

# This script lives in the repo root and packages the repo itself.
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$SOURCE_DIR = $SCRIPT_DIR
$OUT        = Join-Path $SCRIPT_DIR "portable-build"   # generated, git-ignored

$PYTHON_DIR = Join-Path $OUT "python"
$APP_DIR    = Join-Path $OUT "app"
$MODEL_DIR  = Join-Path $OUT "model"
$CACHE_DIR  = Join-Path $OUT "cache"

# Set by Resolve-Frontend to the chosen frontend bundle directory.
$script:FRONTEND_DIST_SRC = $null

$PYTHON_EMBED_URL = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$PYTHON_EMBED_ZIP = Join-Path $env:TEMP "python-embed-$PythonVersion.zip"
$GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
$GET_PIP_SCRIPT = Join-Path $env:TEMP "get-pip.py"

# Embeddable Python names its files by major+minor only, e.g. 3.12.10 ->
# "python312" (python312.zip / python312._pth).
$_verParts = $PythonVersion.Split('.')
$PYVER_SHORT = "python$($_verParts[0])$($_verParts[1])"

# ============================================================
#  LOG FUNCTIONS
# ============================================================

function Write-Step  { param([string]$M); Write-Host "`n=== $M ===" -ForegroundColor Cyan }
function Write-OK    { param([string]$M); Write-Host "  [OK] $M" -ForegroundColor Green }
function Write-Warn  { param([string]$M); Write-Host "  [!] $M" -ForegroundColor Yellow }
function Write-Fail  { param([string]$M); Write-Host "  [X] $M" -ForegroundColor Red }
function Write-Info  { param([string]$M); Write-Host "  [*] $M" -ForegroundColor Gray }

# ============================================================
#  PHASE 1: DOWNLOAD PYTHON EMBEDDABLE
# ============================================================

function Get-PythonEmbeddable {
    Write-Step "PHASE 1: DOWNLOADING PYTHON EMBEDDABLE"

    New-Item -ItemType Directory -Path $OUT -Force | Out-Null

    if (Test-Path "$PYTHON_DIR\python.exe") {
        if (-not $Force) {
            Write-OK "Python embeddable already present at $PYTHON_DIR"
            return $true
        }
        Write-Warn "Forcing re-download..."
        Remove-Item -Recurse -Force $PYTHON_DIR -ErrorAction SilentlyContinue
    }

    Write-Info "Downloading Python $PythonVersion embeddable package..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $PYTHON_EMBED_URL -OutFile $PYTHON_EMBED_ZIP -UseBasicParsing -TimeoutSec 300
    Write-OK "Download complete"

    Write-Info "Extracting to $PYTHON_DIR..."
    Expand-Archive -Path $PYTHON_EMBED_ZIP -DestinationPath $PYTHON_DIR -Force
    Remove-Item $PYTHON_EMBED_ZIP -Force -ErrorAction SilentlyContinue
    Write-OK "Extracted"

    $dllsDir = Join-Path $PYTHON_DIR "DLLs"
    if (-not (Test-Path $dllsDir)) {
        New-Item -ItemType Directory -Path $dllsDir -Force | Out-Null
    }
    return $true
}

# ============================================================
#  PHASE 2: CONFIGURE PYTHON (._pth + pip)
# ============================================================

function Enable-PythonSitePackages {
    Write-Step "PHASE 2: CONFIGURING PYTHON"

    $pthFile = Join-Path $PYTHON_DIR "$PYVER_SHORT._pth"
    if (-not (Test-Path $pthFile)) {
        Write-Fail "_pth file not found: $pthFile"
        return $false
    }

    # Enable site-packages AND put the sibling app/ directory on sys.path so the
    # `server` package is importable. With a ._pth file present, the embeddable
    # Python ignores PYTHONPATH, so the search path must be declared here.
    Write-Info "Modifying $PYVER_SHORT._pth (site-packages + ..\app)..."
    $pthContent = @"
$PYVER_SHORT.zip
.
Lib
Lib\site-packages
Scripts
..\app

import site
"@
    # IMPORTANT: write WITHOUT a BOM. Windows PowerShell 5.1's "-Encoding UTF8"
    # prepends a BOM, which would corrupt the first line (python312.zip) and make
    # Python fail with "No module named 'encodings'". ASCII is BOM-free.
    Set-Content -Path $pthFile -Value $pthContent -Encoding ASCII
    Write-OK "_pth file updated"

    $pipExe = Join-Path $PYTHON_DIR "Scripts\pip.exe"
    if (Test-Path $pipExe) {
        Write-OK "pip already installed"
        return $true
    }

    Write-Info "Downloading get-pip.py..."
    Invoke-WebRequest -Uri $GET_PIP_URL -OutFile $GET_PIP_SCRIPT -UseBasicParsing -TimeoutSec 120
    Write-OK "Downloaded get-pip.py"

    Write-Info "Installing pip..."
    $pyExe = Join-Path $PYTHON_DIR "python.exe"
    $proc = Start-Process -FilePath $pyExe -ArgumentList $GET_PIP_SCRIPT -Wait -PassThru -NoNewWindow
    Remove-Item $GET_PIP_SCRIPT -Force -ErrorAction SilentlyContinue

    if ($proc.ExitCode -ne 0 -or -not (Test-Path $pipExe)) {
        Write-Fail "pip installation failed (exit code $($proc.ExitCode))"
        return $false
    }
    Write-OK "pip installed successfully"
    return $true
}

# ============================================================
#  PHASE 3: INSTALL BACKEND DEPENDENCIES
# ============================================================

function Install-Dependencies {
    Write-Step "PHASE 3: INSTALLING BACKEND DEPENDENCIES"

    if ($SkipDeps) {
        Write-Warn "Skipping dependency installation (-SkipDeps)"
        return $true
    }

    $pyExe = Join-Path $PYTHON_DIR "python.exe"

    Write-Info "Upgrading pip..."
    & $pyExe -m pip install --upgrade pip 2>&1 | Out-Null
    Write-OK "pip upgraded"

    # 1) CPU-only PyTorch first (smallest, reproducible; satisfies opf's torch).
    Write-Info "Installing CPU-only PyTorch..."
    & $pyExe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    if ($LASTEXITCODE -ne 0) { Write-Fail "torch install failed"; return $false }
    Write-OK "PyTorch (CPU) installed"

    # 2) The OPF model package from this repo (pulls huggingface_hub<0.25,
    #    safetensors, tiktoken, numpy, packaging).
    Write-Info "Installing OPF model package..."
    & $pyExe -m pip install (Join-Path $SOURCE_DIR "privacy-filter")
    if ($LASTEXITCODE -ne 0) { Write-Fail "opf install failed"; return $false }
    Write-OK "OPF package installed"

    # 3) Backend web deps from requirements-server.txt (single source of truth).
    Write-Info "Installing FastAPI backend dependencies..."
    & $pyExe -m pip install -r (Join-Path $SOURCE_DIR "requirements-server.txt")
    if ($LASTEXITCODE -ne 0) { Write-Fail "backend deps install failed"; return $false }
    Write-OK "Backend dependencies installed"

    Write-Info "Cleaning pip cache..."
    & $pyExe -m pip cache purge 2>&1 | Out-Null
    Write-OK "Cache cleaned"
    return $true
}

# ============================================================
#  PHASE 4: RESOLVE REACT FRONTEND (built dist; needs Node only to (re)build)
# ============================================================

function Build-FrontendWithNode {
    $frontendSrc = Join-Path $SOURCE_DIR "frontend"
    if (-not (Test-Path (Join-Path $frontendSrc "package.json"))) {
        Write-Fail "frontend/ not found at $frontendSrc"
        return $false
    }
    $env:COREPACK_ENABLE_DOWNLOAD_PROMPT = "0"
    Push-Location $frontendSrc
    Write-Info "Installing frontend packages (pnpm)..."
    & corepack pnpm install --frozen-lockfile 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { & corepack pnpm install 2>&1 | Out-Null }
    Write-Info "Building production bundle..."
    & corepack pnpm run build 2>&1 | Out-Null
    Pop-Location
    return (Test-Path (Join-Path $frontendSrc "dist\index.html"))
}

function Resolve-Frontend {
    Write-Step "PHASE 4: RESOLVING REACT FRONTEND"

    $sourceDist = Join-Path $SOURCE_DIR "frontend\dist"
    $hasNode = ($null -ne (Get-Command node -ErrorAction SilentlyContinue)) -and `
               ($null -ne (Get-Command corepack -ErrorAction SilentlyContinue))

    if ($RebuildFrontend) {
        if (-not $hasNode) {
            Write-Fail "-RebuildFrontend requires Node.js + corepack."
            return $false
        }
        Write-Info "Rebuilding frontend with Node (-RebuildFrontend)..."
        if (-not (Build-FrontendWithNode)) { Write-Fail "Frontend build failed"; return $false }
        $script:FRONTEND_DIST_SRC = $sourceDist
        Write-OK "Frontend rebuilt"
        return $true
    }

    # Reuse an existing build (no Node needed).
    if (Test-Path (Join-Path $sourceDist "index.html")) {
        $script:FRONTEND_DIST_SRC = $sourceDist
        Write-OK "Using existing frontend\dist (no Node needed)"
        return $true
    }

    # No build yet: build it if Node is available.
    if ($hasNode) {
        Write-Info "No frontend\dist found; building it..."
        if (-not (Build-FrontendWithNode)) { Write-Fail "Frontend build failed"; return $false }
        $script:FRONTEND_DIST_SRC = $sourceDist
        Write-OK "Frontend built"
        return $true
    }

    Write-Fail "frontend\dist is missing and Node.js is not installed."
    Write-Host "  Build the UI once (needs Node), then re-run:" -ForegroundColor Yellow
    Write-Host "    corepack pnpm -C frontend install" -ForegroundColor Yellow
    Write-Host "    corepack pnpm -C frontend run build" -ForegroundColor Yellow
    return $false
}

# ============================================================
#  PHASE 5: COPY APPLICATION CODE + FRONTEND BUILD
# ============================================================

function Copy-ApplicationCode {
    Write-Step "PHASE 5: COPYING APPLICATION CODE"

    if (Test-Path $APP_DIR) {
        Remove-Item -Recurse -Force $APP_DIR -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Path $APP_DIR -Force | Out-Null

    # Backend package
    $serverSrc = Join-Path $SOURCE_DIR "server"
    if (-not (Test-Path $serverSrc)) {
        Write-Fail "server/ not found in the repo."
        return $false
    }
    Copy-Item -Recurse $serverSrc (Join-Path $APP_DIR "server") -Force
    Get-ChildItem -Path (Join-Path $APP_DIR "server") -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-OK "Copied server/"

    foreach ($file in @("app_update.py", "model_update.py", "VERSION")) {
        $src = Join-Path $SOURCE_DIR $file
        if (Test-Path $src) {
            Copy-Item $src (Join-Path $APP_DIR $file) -Force
            Write-OK "Copied $file"
        } else {
            Write-Warn "Source not found: $src"
        }
    }

    # Built frontend (resolved in Phase 4)
    $distSrc = $script:FRONTEND_DIST_SRC
    if (-not $distSrc -or -not (Test-Path (Join-Path $distSrc "index.html"))) {
        Write-Fail "Frontend bundle was not resolved (Phase 4 must run first)."
        return $false
    }
    $distDst = Join-Path $APP_DIR "frontend\dist"
    New-Item -ItemType Directory -Path $distDst -Force | Out-Null
    Copy-Item -Recurse (Join-Path $distSrc "*") $distDst -Force
    Write-OK "Copied frontend bundle"

    New-Item -ItemType Directory -Path $MODEL_DIR -Force | Out-Null

    Write-OK "Application code copied"
    return $true
}

# ============================================================
#  PHASE 6: (OPTIONAL) PRE-DOWNLOAD MODEL
# ============================================================

function Get-ModelOffline {
    Write-Step "PHASE 6: PRE-DOWNLOADING MODEL (-IncludeModel)"

    if (-not $IncludeModel) {
        Write-Info "Skipped (model is downloaded on first run). Use -IncludeModel to bundle it."
        return $true
    }

    $pyExe = Join-Path $PYTHON_DIR "python.exe"
    $env:OPF_CHECKPOINT = $MODEL_DIR
    $env:HF_HOME = Join-Path $CACHE_DIR "huggingface"
    New-Item -ItemType Directory -Path $env:HF_HOME -Force | Out-Null

    Write-Info "Downloading model into $MODEL_DIR (this is large, ~2.7 GB)..."
    $code = "import sys; sys.path.insert(0, r'$APP_DIR'); import model_update; ok,msg = model_update.download_model_update(); print(msg); sys.exit(0 if ok else 1)"
    & $pyExe -c $code
    if ($LASTEXITCODE -ne 0) { Write-Fail "Model download failed"; return $false }
    Write-OK "Model bundled"
    return $true
}

# ============================================================
#  PHASE 7: CREATE LAUNCHER SCRIPTS (inside portable-build/)
# ============================================================

function New-LauncherScripts {
    Write-Step "PHASE 7: CREATING LAUNCHER SCRIPTS"

    # Browser opener: waits until the server is listening, then opens it. If the
    # server never comes up (startup crash), opens the local error.html instead.
    $openBrowser = @'
"""Open the app when the server is up; otherwise open the local error page."""
import os, socket, time, webbrowser
from pathlib import Path

host = os.environ.get("PF_HOST", "127.0.0.1")
if host in ("", "0.0.0.0"):
    host = "127.0.0.1"
port = int(os.environ.get("PF_PORT", "7860"))

up = False
for _ in range(180):  # up to ~90 s
    try:
        with socket.create_connection((host, port), timeout=1):
            up = True
            break
    except OSError:
        time.sleep(0.5)

if up:
    webbrowser.open("http://localhost:%d" % port)
else:
    err = Path(__file__).resolve().parent / "error.html"
    if err.is_file():
        webbrowser.open(err.as_uri())
'@
    Set-Content -Path (Join-Path $APP_DIR "_open_browser.py") -Value $openBrowser -Encoding ASCII
    Write-OK "Created app\_open_browser.py"

    $errorHtml = @'
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><title>Privacy Filter - could not start</title>
<style>
 body{font-family:system-ui,Segoe UI,sans-serif;background:#0f1115;color:#e6e8ec;
      max-width:640px;margin:40px auto;padding:0 16px;line-height:1.5}
 code{background:#1f232c;padding:2px 6px;border-radius:4px}
 .muted{color:#9aa3b2}
</style>
</head>
<body>
<h1>Privacy Filter could not start</h1>
<p>The local server did not start in time, so the app could not open.</p>
<h3>What to do</h3>
<ol>
 <li>Run <code>start.bat</code> (in this folder) to see the error in a console window.</li>
 <li>Send the log file for support: <code>logs\privacy-filter.log</code>.</li>
 <li>Make sure no antivirus/firewall is blocking the bundled Python.</li>
</ol>
<p class="muted">Everything stays inside this folder; you can delete it anytime.</p>
</body>
</html>
'@
    Set-Content -Path (Join-Path $APP_DIR "error.html") -Value $errorHtml -Encoding ASCII
    Write-OK "Created app\error.html"

    # Shared env block reused by the .bat files (%~dp0 = the portable folder).
    $envBlock = @'
set "OPF_CHECKPOINT=%~dp0model"
set "HF_HOME=%~dp0cache\huggingface"
set "HUGGINGFACE_HUB_CACHE=%~dp0cache\huggingface\hub"
set "TIKTOKEN_CACHE_DIR=%~dp0cache\tiktoken"
set "TMP=%~dp0tmp"
set "TEMP=%~dp0tmp"
set "PF_LOG_DIR=%~dp0logs"
set "PF_HOST=127.0.0.1"
set "PF_PORT=7860"
if not exist "%~dp0model" mkdir "%~dp0model"
if not exist "%~dp0cache" mkdir "%~dp0cache"
if not exist "%~dp0tmp" mkdir "%~dp0tmp"
if not exist "%~dp0logs" mkdir "%~dp0logs"
'@

    # launch.bat: HIDDEN launcher (run by the .vbs). Starts the server windowless
    # (pythonw = no console) plus the browser opener, then exits; processes persist.
    $launchBat = @"
@echo off
cd /d "%~dp0"
$envBlock
start "" "%~dp0python\pythonw.exe" "%~dp0app\_open_browser.py"
start "" "%~dp0python\pythonw.exe" -m server.main
"@
    Set-Content -Path (Join-Path $OUT "launch.bat") -Value $launchBat -Encoding ASCII
    Write-OK "Created launch.bat"

    # Privacy Filter.vbs: friendly double-click entry; runs launch.bat hidden.
    $vbs = @'
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
batPath = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "launch.bat")
sh.Run """" & batPath & """", 0, False
'@
    Set-Content -Path (Join-Path $OUT "Privacy Filter.vbs") -Value $vbs -Encoding ASCII
    Write-OK "Created Privacy Filter.vbs"

    # start.bat: VISIBLE launcher for debugging (shows server logs / errors).
    $startBat = @"
@echo off
title Privacy Filter - Portable (debug)
cd /d "%~dp0"
$envBlock
echo.
echo ========================================
echo   Privacy Filter - Portable (debug console)
echo ========================================
echo.
echo If Windows asks about the firewall, click "Allow access".
echo The browser opens automatically when the server is ready.
echo The first run downloads the model (~2.7 GB); progress shows in the app.
echo Press Ctrl+C to stop.
echo.
start "" /b "%~dp0python\python.exe" "%~dp0app\_open_browser.py"
"%~dp0python\python.exe" -m server.main
pause
"@
    Set-Content -Path (Join-Path $OUT "start.bat") -Value $startBat -Encoding ASCII
    Write-OK "Created start.bat (debug)"

    # uninstall.bat
    $uninstallBat = @'
@echo off
echo.
echo ========================================
echo   Privacy Filter - Uninstall
echo ========================================
echo.
echo This removes the portable package contents in this folder:
echo   python\  app\  model\  cache\  tmp\  logs\
echo.
set /p confirm="Are you sure? (Y/N): "
if /i not "%confirm%"=="Y" (
    echo Cancelled.
    pause
    exit /b
)
rd /s /q "%~dp0python" 2>nul
rd /s /q "%~dp0app" 2>nul
rd /s /q "%~dp0model" 2>nul
rd /s /q "%~dp0cache" 2>nul
rd /s /q "%~dp0tmp" 2>nul
rd /s /q "%~dp0logs" 2>nul
echo.
echo Done. You can now delete this folder safely.
pause
'@
    Set-Content -Path (Join-Path $OUT "uninstall.bat") -Value $uninstallBat -Encoding ASCII
    Write-OK "Created uninstall.bat"
    return $true
}

# ============================================================
#  MAIN
# ============================================================

function Main {
    Clear-Host
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host "  Privacy Filter - Portable Builder (single repo)" -ForegroundColor Cyan
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Repo:          $SOURCE_DIR"
    Write-Host "  Output:        $OUT"
    Write-Host "  Python:        $PythonVersion"
    Write-Host "  Include model: $IncludeModel"
    Write-Host ""

    $startTime = Get-Date

    if (-not (Test-Path (Join-Path $SOURCE_DIR "server"))) {
        Write-Host "[FATAL] This does not look like the project root (no server/)." -ForegroundColor Red
        exit 1
    }

    if (-not (Get-PythonEmbeddable))      { Write-Host "`n[FATAL] Python embeddable." -ForegroundColor Red; exit 1 }
    if (-not (Enable-PythonSitePackages)) { Write-Host "`n[FATAL] Configure Python." -ForegroundColor Red; exit 1 }
    if (-not (Install-Dependencies))      { Write-Host "`n[FATAL] Install deps." -ForegroundColor Red; exit 1 }
    if (-not (Resolve-Frontend))          { Write-Host "`n[FATAL] Resolve frontend." -ForegroundColor Red; exit 1 }
    if (-not (Copy-ApplicationCode))      { Write-Host "`n[FATAL] Copy app code." -ForegroundColor Red; exit 1 }
    if (-not (Get-ModelOffline))          { Write-Host "`n[FATAL] Pre-download model." -ForegroundColor Red; exit 1 }
    if (-not (New-LauncherScripts))       { Write-Host "`n[FATAL] Launchers." -ForegroundColor Red; exit 1 }

    $elapsed = (Get-Date) - $startTime
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "  BUILD COMPLETE ($($elapsed.Minutes)m $($elapsed.Seconds)s)" -ForegroundColor Green
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Portable package ready at:" -ForegroundColor White
    Write-Host "    $OUT" -ForegroundColor White
    Write-Host "  Copy that folder anywhere and double-click 'Privacy Filter.vbs'." -ForegroundColor White
    if (-not $IncludeModel) {
        Write-Host "  First run downloads the model (~2.7 GB); progress shows in the app." -ForegroundColor Yellow
    }
    Write-Host "  Everything (model, caches, temp, logs) stays inside that folder." -ForegroundColor Gray
    Write-Host ""
}

Main
