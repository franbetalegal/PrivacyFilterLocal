#Requires -Version 5.1
<#
.SYNOPSIS
    Complete installer for Privacy Filter with local web interface.
.DESCRIPTION
    Checks and installs all required dependencies with multiple fallbacks.
    Clones from franbetalegal/PrivacyFilterLocal and sets up a Python venv.
.PARAMETER Force
    Force reinstallation/overwrite of existing files.
.PARAMETER NoRun
    Install only, do not launch the application.
.PARAMETER PythonVersion
    Python version to download when not already installed (e.g. "3.12.8").
.PARAMETER GitVersion
    Git for Windows version to download when not already installed, in the
    format "X.Y.Z.W" where the trailing number is the Windows build number
    (e.g. "2.47.1.2"). The release tag is derived as "vX.Y.Z.windows.W".
.EXAMPLE
    .\install.ps1
    .\install.ps1 -Force
    .\install.ps1 -PythonVersion "3.12.10" -GitVersion "2.48.1.1"
#>

param(
    [switch]$Force,
    [switch]$NoRun,
    [string]$PythonVersion = "3.12.8",
    [string]$GitVersion = "2.47.1.2"
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# ============================================================
#  CONFIGURATION
# ============================================================

$PROJECT_DIR = "C:\privacy-filter"
$REPO_DIR = "$PROJECT_DIR\privacy-filter"
$REPO_URL = "https://github.com/franbetalegal/PrivacyFilterLocal.git"
$PYTHON_URL = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
$PYTHON_INSTALLER = "$env:TEMP\python-installer-$($PythonVersion -replace '\.','').exe"

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

    if (Test-CommandExists "winget") {
        Write-Info "Trying winget..."
        $null = winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent 2>&1
        Refresh-Path
        $check = Test-PythonReal
        if ($check.Found) { Write-OK "Python installed via winget: $($check.Version)"; return $true }
        Write-Warn "winget failed"
    }

    if (Test-CommandExists "choco") {
        Write-Info "Trying chocolatey..."
        $null = choco install python -y 2>&1
        Refresh-Path
        $check = Test-PythonReal
        if ($check.Found) { Write-OK "Python installed via choco: $($check.Version)"; return $true }
        Write-Warn "chocolatey failed"
    }

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

    if (Test-CommandExists "winget") {
        Write-Info "Trying winget..."
        $null = winget install Git.Git --accept-source-agreements --accept-package-agreements --silent 2>&1
        Refresh-Path
        if (Test-CommandExists "git") { Write-OK "Git installed via winget"; return $true }
        Write-Warn "winget failed"
    }

    if (Test-CommandExists "choco") {
        Write-Info "Trying chocolatey..."
        $null = choco install git -y 2>&1
        Refresh-Path
        if (Test-CommandExists "git") { Write-OK "Git installed via choco"; return $true }
        Write-Warn "chocolatey failed"
    }

    Write-Info "Downloading Git..."
    # GitVersion format: "X.Y.Z.W" (semver + Windows build number).
    # Tag:   vX.Y.Z.windows.W
    # Asset: Git-X.Y.Z.W-64-bit.exe
    $gitSemver = $GitVersion -replace '\.\d+$', ''
    $gitUrl = "https://github.com/git-for-windows/git/releases/download/v$gitSemver.windows.$($GitVersion -replace '.*\.', '')/Git-$GitVersion-64-bit.exe"
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
#  CLONE REPOSITORY
# ============================================================

function Clone-Repository {
    Write-Step "CLONING REPOSITORY"

    Write-Info "Checking connectivity to GitHub..."
    try {
        $response = Invoke-WebRequest -Uri "https://github.com" -UseBasicParsing -TimeoutSec 10 -Method Head
        Write-OK "Connectivity OK"
    } catch {
        Write-Fail "Could not connect to GitHub. Check your internet connection."
        return $false
    }

    # Check if project directory already exists with complete repo
    if (Test-Path "$PROJECT_DIR\.git") {
        Write-Warn "Repository already exists at $PROJECT_DIR"

        $hasAppLocal = Test-Path "$PROJECT_DIR\app_local.py"
        $hasPrivacyFilter = Test-Path "$PROJECT_DIR\privacy-filter\opf"

        if ($hasAppLocal -and $hasPrivacyFilter) {
            Write-OK "Repository verified (complete)"
            if (-not $Force) {
                return $true
            }
            Write-Warn "Forcing re-clone..."
        } else {
            Write-Warn "Repository incomplete. Removing..."
        }

        Remove-Item -Recurse -Force $PROJECT_DIR -ErrorAction SilentlyContinue
    }

    # Remove empty directory if it exists
    if (Test-Path $PROJECT_DIR) {
        Remove-Item -Recurse -Force $PROJECT_DIR -ErrorAction SilentlyContinue
    }

    # Clone
    Write-Info "Cloning from $REPO_URL..."
    $gitOutput = & git clone $REPO_URL $PROJECT_DIR 2>&1 | Out-String

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Error during git clone:"
        Write-Host $gitOutput -ForegroundColor Red
        return $false
    }

    # Verify clone
    $checks = @{
        ".git"              = Test-Path "$PROJECT_DIR\.git"
        "app_local.py"      = Test-Path "$PROJECT_DIR\app_local.py"
        "privacy-filter/opf"= Test-Path "$PROJECT_DIR\privacy-filter\opf"
        "start.bat"         = Test-Path "$PROJECT_DIR\start.bat"
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
#  CREATE VIRTUAL ENVIRONMENT
# ============================================================

function New-VirtualEnv {
    Write-Step "CREATING VIRTUAL ENVIRONMENT"

    $py = Test-PythonReal
    if (-not $py.Found) {
        Write-Fail "Python not found"
        return $false
    }

    $venvDir = "$PROJECT_DIR\.venv"

    if (Test-Path "$venvDir\Scripts\python.exe") {
        Write-Warn "Virtual environment already exists"
        if (-not $Force) {
            Write-OK "Using existing venv"
            return $true
        }
        Write-Warn "Recreating venv..."
        Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
    }

    Write-Info "Creating venv with $($py.Path)..."
    & $py.Path -m venv $venvDir 2>&1 | Out-Null

    if (Test-Path "$venvDir\Scripts\python.exe") {
        Write-OK "Virtual environment created"
        return $true
    } else {
        Write-Fail "Failed to create virtual environment"
        return $false
    }
}

# ============================================================
#  INSTALL PYTHON DEPENDENCIES
# ============================================================

function Install-Dependencies {
    Write-Step "INSTALLING PYTHON DEPENDENCIES"

    $venvPip = "$PROJECT_DIR\.venv\Scripts\pip.exe"
    $venvPython = "$PROJECT_DIR\.venv\Scripts\python.exe"

    if (-not (Test-Path $venvPip)) {
        Write-Fail "pip not found in venv"
        return $false
    }

    # Update pip
    Write-Info "Updating pip..."
    & $venvPython -m pip install --upgrade pip 2>&1 | Out-Null
    Write-OK "pip updated"

    # Install project in editable mode
    Write-Info "Installing project dependencies..."
    Push-Location $PROJECT_DIR\privacy-filter
    $output = & $venvPip install -e . 2>&1 | Out-String
    Pop-Location

    if ($LASTEXITCODE -eq 0) {
        Write-OK "Project dependencies installed"
    } else {
        Write-Warn "Some dependencies may have failed"
        Write-Info $output
    }

    # Web interface dependencies.
    # Only packages NOT covered by pyproject.toml are listed here.
    # Gradio 6.15.1 is the latest stable version with all tab-switching
    # bugs fixed and security vulnerabilities resolved.
    Write-Info "Installing web interface dependencies..."
    $webDeps = @(
        "gradio==6.15.1",
        "PyMuPDF",
        "python-docx"
    )
    foreach ($dep in $webDeps) {
        $output = & $venvPip install $dep 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) {
            Write-OK "$dep installed"
        } else {
            Write-Fail "Error installing $dep"
            Write-Info $output
            return $false
        }
    }

    return $true
}

# ============================================================
#  MAIN
# ============================================================

function Main {
    Clear-Host

    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host "  Privacy Filter - Complete Installer" -ForegroundColor Cyan
    Write-Host "================================================================" -ForegroundColor Cyan

    $startTime = Get-Date

    # PHASE 0: Initial diagnostics
    Write-Step "PHASE 0: SYSTEM DIAGNOSTICS"

    $py = Test-PythonReal
    if ($py.Found) {
        Write-OK "Python found: $($py.Version)"
    } else {
        Write-Warn "Python NOT found"
    }

    if (Test-CommandExists "git") {
        Write-OK "Git found: $(git --version)"
    } else {
        Write-Warn "Git NOT found"
    }

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

    # PHASE 4: Virtual Environment
    if (-not (New-VirtualEnv)) {
        Write-Host "`n[FATAL] Could not create virtual environment." -ForegroundColor Red
        exit 1
    }

    # PHASE 5: Dependencies
    if (-not (Install-Dependencies)) {
        Write-Host "`n[FATAL] Could not install dependencies." -ForegroundColor Red
        exit 1
    }

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
        & "$PROJECT_DIR\.venv\Scripts\python.exe" app_local.py
        Pop-Location
    } else {
        Write-Host ""
        Write-Host "To run:" -ForegroundColor Cyan
        Write-Host "  cd $PROJECT_DIR" -ForegroundColor White
        Write-Host "  .venv\Scripts\python.exe app_local.py" -ForegroundColor White
        Write-Host ""
    }
}

Main
