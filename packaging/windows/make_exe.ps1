#Requires -Version 5.1
<#
.SYNOPSIS
    Wrap the portable build into a single self-extracting .exe using NSIS.
.DESCRIPTION
    Assumes .\portable-build already exists (run build_portable.ps1 first, or
    pass -Build). Produces dist\PrivacyFilter-Setup-v<version>.exe.

    The resulting .exe extracts the app into a "PrivacyFilter" subfolder next to
    itself and launches it - no admin rights, no registry, no traditional
    install. Uninstall = delete that subfolder.
.PARAMETER Version
    Version string for the output name / exe metadata. Defaults to the VERSION
    file in the repo root.
.PARAMETER Build
    Run build_portable.ps1 -RebuildFrontend first (otherwise an existing
    portable-build\ is reused).
.EXAMPLE
    .\packaging\windows\make_exe.ps1
    .\packaging\windows\make_exe.ps1 -Build
#>
param(
    [string]$Version,
    [switch]$Build
)

$ErrorActionPreference = "Stop"

# packaging\windows -> packaging -> repo root
$repo     = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$portable = Join-Path $repo "portable-build"
$nsi      = Join-Path $PSScriptRoot "installer.nsi"
$distDir  = Join-Path $repo "dist"

if (-not $Version) {
    $Version = (Get-Content (Join-Path $repo "VERSION") -Raw).Trim()
}

if ($Build) {
    Write-Host "Building portable package..." -ForegroundColor Cyan
    & (Join-Path $repo "build_portable.ps1") -RebuildFrontend
    if ($LASTEXITCODE -ne 0) { throw "build_portable.ps1 failed (exit $LASTEXITCODE)" }
}

if (-not (Test-Path (Join-Path $portable "Privacy Filter.vbs"))) {
    throw "portable-build not found or incomplete at $portable. Run build_portable.ps1 first (or pass -Build)."
}

# Locate makensis: PATH first, then the standard install locations.
$makensis = (Get-Command makensis.exe -ErrorAction SilentlyContinue).Source
if (-not $makensis) {
    foreach ($c in @("$env:ProgramFiles\NSIS\makensis.exe",
                     "${env:ProgramFiles(x86)}\NSIS\makensis.exe")) {
        if (Test-Path $c) { $makensis = $c; break }
    }
}
if (-not $makensis) {
    throw "makensis (NSIS) not found. Install it with 'choco install nsis -y' or from https://nsis.sourceforge.io."
}

New-Item -ItemType Directory -Path $distDir -Force | Out-Null
$outFile = Join-Path $distDir "PrivacyFilter-Setup-v$Version.exe"

Write-Host "Building $outFile with NSIS ($makensis)..." -ForegroundColor Cyan
# Inner-quote path defines so values with spaces survive makensis re-parsing.
& $makensis "/DVERSION=$Version" "/DSRCDIR=`"$portable`"" "/DOUTFILE=`"$outFile`"" $nsi
if ($LASTEXITCODE -ne 0) { throw "makensis failed (exit $LASTEXITCODE)" }

Write-Host "Created: $outFile" -ForegroundColor Green
