#Requires -Version 5.1
<#
.SYNOPSIS
    Instalador completo de OpenAI Privacy Filter con interfaz web local.
.DESCRIPTION
    Comprueba e instala todas las dependencias necesarias con multiples fallbacks.
.PARAMETER Force
    Forzar reinstalacion/sobrescritura de archivos existentes.
.PARAMETER NoRun
    Solo instalar, no ejecutar la aplicacion.
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
#  CONFIGURACION
# ============================================================

$PROJECT_DIR = "C:\privacy-filter"
$REPO_DIR = "$PROJECT_DIR\privacy-filter"
$REPO_URL = "https://github.com/openai/privacy-filter.git"
$PYTHON_URL = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
$PYTHON_INSTALLER = "$env:TEMP\python-installer-312.exe"

# ============================================================
#  FUNCIONES DE LOG
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
#  DETECCION DE PYTHON
# ============================================================

function Test-PythonReal {
    <#
    .SYNOPSIS
        Busca un Python 3.10+ real (no stub de Windows Store).
    #>
    # Buscar en comandos del PATH
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

    # Buscar en rutas conocidas
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
#  INSTALACION DE PYTHON (3 metodos con fallback)
# ============================================================

function Install-Python {
    Write-Step "INSTALANDO PYTHON"

    # Metodo 1: winget
    if (Test-CommandExists "winget") {
        Write-Info "Intentando con winget..."
        $null = winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent 2>&1
        Refresh-Path
        $check = Test-PythonReal
        if ($check.Found) { Write-OK "Python instalado via winget: $($check.Version)"; return $true }
        Write-Warn "winget no funciono"
    }

    # Metodo 2: chocolatey
    if (Test-CommandExists "choco") {
        Write-Info "Intentando con chocolatey..."
        $null = choco install python -y 2>&1
        Refresh-Path
        $check = Test-PythonReal
        if ($check.Found) { Write-OK "Python instalado via choco: $($check.Version)"; return $true }
        Write-Warn "chocolatey no funciono"
    }

    # Metodo 3: Descarga directa
    Write-Info "Descargando Python desde python.org..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $PYTHON_URL -OutFile $PYTHON_INSTALLER -UseBasicParsing -TimeoutSec 300
        Write-OK "Descarga completada"

        Write-Info "Instalando en silencio (puede tardar 1-2 minutos)..."
        $proc = Start-Process -FilePath $PYTHON_INSTALLER -ArgumentList @(
            "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_pip=1",
            "Include_test=0", "TargetDir=C:\Python312", "CompileAll=0"
        ) -Wait -PassThru -NoNewWindow

        Remove-Item $PYTHON_INSTALLER -Force -ErrorAction SilentlyContinue

        if ($proc.ExitCode -eq 0 -or $proc.ExitCode -eq 3010) {
            Refresh-Path
            $check = Test-PythonReal
            if ($check.Found) { Write-OK "Python instalado via descarga: $($check.Version)"; return $true }
        }
        Write-Fail "El instalador termino con codigo $($proc.ExitCode)"
    } catch {
        Write-Fail "Error: $_"
        Remove-Item $PYTHON_INSTALLER -Force -ErrorAction SilentlyContinue
    }

    return $false
}

# ============================================================
#  INSTALACION DE GIT (3 metodos con fallback)
# ============================================================

function Install-Git {
    Write-Step "INSTALANDO GIT"

    # Metodo 1: winget
    if (Test-CommandExists "winget") {
        Write-Info "Intentando con winget..."
        $null = winget install Git.Git --accept-source-agreements --accept-package-agreements --silent 2>&1
        Refresh-Path
        if (Test-CommandExists "git") { Write-OK "Git instalado via winget"; return $true }
        Write-Warn "winget no funciono"
    }

    # Metodo 2: chocolatey
    if (Test-CommandExists "choco") {
        Write-Info "Intentando con chocolatey..."
        $null = choco install git -y 2>&1
        Refresh-Path
        if (Test-CommandExists "git") { Write-OK "Git instalado via choco"; return $true }
        Write-Warn "chocolatey no funciono"
    }

    # Metodo 3: Descarga directa
    Write-Info "Descargando Git..."
    $gitUrl = "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.2/Git-2.47.1.2-64-bit.exe"
    $gitInstaller = "$env:TEMP\git-installer.exe"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $gitUrl -OutFile $gitInstaller -UseBasicParsing -TimeoutSec 300
        Write-OK "Descarga completada"

        Write-Info "Instalando en silencio..."
        $proc = Start-Process -FilePath $gitInstaller -ArgumentList @(
            "/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-",
            "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS",
            "/COMPONENTS=icons,ext,ext\shellhere,ext\guihere,gitlfs,assoc,assoc_sh"
        ) -Wait -PassThru -NoNewWindow

        Remove-Item $gitInstaller -Force -ErrorAction SilentlyContinue

        if ($proc.ExitCode -eq 0 -or $proc.ExitCode -eq 3010) {
            Refresh-Path
            if (Test-CommandExists "git") { Write-OK "Git instalado via descarga"; return $true }
        }
        Write-Fail "El instalador termino con codigo $($proc.ExitCode)"
    } catch {
        Write-Fail "Error: $_"
        Remove-Item $gitInstaller -Force -ErrorAction SilentlyContinue
    }

    return $false
}

# ============================================================
#  CLONAR REPOSITORIO (con verificacion completa)
# ============================================================

function Clone-Repository {
    Write-Step "CLONANDO REPOSITORIO"

    # Verificar conectividad
    Write-Info "Verificando conectividad a GitHub..."
    try {
        $response = Invoke-WebRequest -Uri "https://github.com" -UseBasicParsing -TimeoutSec 10 -Method Head
        Write-OK "Conectividad OK"
    } catch {
        Write-Fail "No se pudo conectar a GitHub. Verifica tu conexion a internet."
        return $false
    }

    # Verificar si el repositorio ya existe y esta completo
    if (Test-Path "$REPO_DIR\.git") {
        Write-Warn "Repositorio ya existe"

        # Verificar que esta completo
        $hasOpf = Test-Path "$REPO_DIR\opf"
        $hasReadme = Test-Path "$REPO_DIR\README.md"
        $hasPyproject = Test-Path "$REPO_DIR\pyproject.toml"

        if ($hasOpf -and $hasReadme -and $hasPyproject) {
            Write-OK "Repositorio verificado (completo)"
            if (-not $Force) {
                return $true
            }
            Write-Warn "Forzando reclonaje..."
        } else {
            Write-Warn "Repositorio incompleto. Eliminando..."
        }

        Remove-Item -Recurse -Force $REPO_DIR -ErrorAction SilentlyContinue
    }

    # Eliminar directorio vacio si existe
    if (Test-Path $REPO_DIR) {
        Remove-Item -Recurse -Force $REPO_DIR -ErrorAction SilentlyContinue
    }

    # Clonar
    Write-Info "Clonando desde $REPO_URL..."
    $gitOutput = & git clone $REPO_URL $REPO_DIR 2>&1 | Out-String

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Error durante git clone:"
        Write-Host $gitOutput -ForegroundColor Red
        return $false
    }

    # Verificar que el clone fue exitoso
    $checks = @{
        ".git"     = Test-Path "$REPO_DIR\.git"
        "opf"      = Test-Path "$REPO_DIR\opf"
        "README"   = Test-Path "$REPO_DIR\README.md"
        "pyproject"= Test-Path "$REPO_DIR\pyproject.toml"
    }

    $allOk = $true
    foreach ($key in $checks.Keys) {
        if ($checks[$key]) {
            Write-OK "$key verificado"
        } else {
            Write-Fail "$key NO encontrado"
            $allOk = $false
        }
    }

    if ($allOk) {
        Write-OK "Repositorio clonado y verificado correctamente"
        return $true
    } else {
        Write-Fail "El repositorio esta incompleto"
        return $false
    }
}

# ============================================================
#  INSTALAR DEPENDENCIAS PYTHON
# ============================================================

function Install-Dependencies {
    Write-Step "INSTALANDO DEPENDENCIAS PYTHON"

    $py = Test-PythonReal
    if (-not $py.Found) {
        Write-Fail "Python no encontrado"
        return $false
    }

    $pythonPath = $py.Path
    $pythonDir = Split-Path $pythonPath
    $pipPath = Join-Path $pythonDir "pip.exe"

    # Si pip.exe no existe, buscar pip3.exe
    if (-not (Test-Path $pipPath)) {
        $pipPath = Join-Path $pythonDir "pip3.exe"
    }

    # Si aun no existe, usar python -m pip
    $useModule = -not (Test-Path $pipPath)
    if ($useModule) {
        Write-Warn "pip.exe no encontrado, usando python -m pip"
        $pipPath = $pythonPath
    }

    # Actualizar pip
    Write-Info "Actualizando pip..."
    if ($useModule) {
        & $pythonPath -m pip install --upgrade pip 2>&1 | Out-Null
    } else {
        & $pipPath install --upgrade pip 2>&1 | Out-Null
    }
    Write-OK "pip actualizado"

    # Instalar dependencias del repositorio
    Write-Info "Instalando dependencias del proyecto..."
    Push-Location $REPO_DIR

    if ($useModule) {
        $output = & $pythonPath -m pip install -e . 2>&1 | Out-String
    } else {
        $output = & $pipPath install -e . 2>&1 | Out-String
    }

    if ($LASTEXITCODE -eq 0) {
        Write-OK "Dependencias del proyecto instaladas"
    } else {
        Write-Warn "Algunas dependencias pudieron fallar"
        Write-Info $output
    }

    # Dependencias de la interfaz web
    Write-Info "Instalando dependencias de la interfaz web..."
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
            Write-OK "$dep instalado"
        } else {
            Write-Warn "Error instalando $dep"
        }
    }

    Pop-Location
    return $true
}

# ============================================================
#  CREAR APP_LOCAL.PY
# ============================================================

function New-AppLocal {
    Write-Step "GENERANDO APP_LOCAL.PY"

    $appPath = "$PROJECT_DIR\app_local.py"

    if ((Test-Path $appPath) -and -not $Force) {
        Write-Warn "app_local.py ya existe (usa -Force para sobrescribir)"
        return
    }

    $appContent = @'
import sys
import time
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
    print("Cargando modelo Privacy Filter...")
    try:
        from opf._api import OPF
        _model = OPF(device="cpu")
        print("[OK] Modelo cargado")
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
        return "", "Introduce texto."
    try:
        model = get_model()
        if model is None:
            return text, "Cargando modelo..."
        start = time.time()
        result = model.redact(text)
        elapsed = time.time() - start
        redacted = result.redacted_text if hasattr(result, 'redacted_text') else str(result)
        spans = result.detected_spans if hasattr(result, 'detected_spans') else []
        if spans:
            summary = f"**{len(spans)} entidades detectadas** ({elapsed:.1f}s)\n\n"
            for s in spans:
                label = s.label if hasattr(s, 'label') else "?"
                txt = s.text if hasattr(s, 'text') else ""
                summary += f"- `{label}`: {txt}\n"
        else:
            summary = f"_No se detectaron entidades PII_ ({elapsed:.1f}s)"
        return redacted, summary
    except Exception as e:
        return text, f"Error: {e}"

def redact_file(file, progress=gr.Progress()):
    if file is None:
        return "_Sube un archivo._", None
    try:
        progress((0, 5), desc="Cargando modelo...")
        model = get_model()
        if model is None:
            return "_Cargando modelo..._", None

        progress((1, 5), desc="Leyendo archivo...")
        path = Path(file.name)
        ext = path.suffix.lower()
        if ext == ".pdf":
            text = extract_text_from_pdf(str(path))
            if text is None:
                return "**Error** leyendo PDF. Instala: `pip install PyMuPDF`", None
        elif ext == ".docx":
            text = extract_text_from_docx(str(path))
            if text is None:
                return "**Error** al leer el DOCX. Revisa la consola.", None
        else:
            text = path.read_text(encoding="utf-8")

        progress((2, 5), desc="Detectando PII...")
        start = time.time()
        result = model.redact(text)
        elapsed = time.time() - start
        redacted = result.redacted_text if hasattr(result, 'redacted_text') else str(result)
        spans = result.detected_spans if hasattr(result, 'detected_spans') else []

        progress((3, 5), desc="Generando enmascaramiento...")
        legend = f"### Resultado del enmascaramiento\n\n"
        legend += f"Procesado en **{elapsed:.1f}s** — **{len(spans)}** entidades detectadas\n\n"
        if spans:
            legend += "| # | Tipo | Original | Reemplazo |\n"
            legend += "|--:|------|----------|----------|\n"
            for i, s in enumerate(spans, 1):
                label = s.label if hasattr(s, 'label') else "?"
                txt = s.text if hasattr(s, 'text') else ""
                ph = s.placeholder if hasattr(s, 'placeholder') else ""
                legend += f"| {i} | `{label}` | {txt} | {ph} |\n"
        else:
            legend += "_No se detectaron entidades PII._"

        progress((4, 5), desc="Creando archivo resultado...")
        if ext == ".pdf" and spans:
            pdf_path = redact_pdf(str(path), spans)
            progress((5, 5), desc="Listo")
            return legend, pdf_path
        if ext == ".docx" and spans:
            docx_path = redact_docx(str(path), spans)
            progress((5, 5), desc="Listo")
            return legend, docx_path
        progress((5, 5), desc="Listo")
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
            progress((1, 3), desc="No hay modelo cacheado, se descargara al usar Detectar.")
            return "_No hay modelo local. Se descargara automaticamente la primera vez que uses Detectar._"
        progress((1, 3), desc="Eliminando modelo actual...")
        shutil.rmtree(str(model_dir))
        progress((2, 3), desc="Descargando modelo actualizado...")
        _model = None
        get_model()
        progress((3, 3), desc="Listo")
        return "_Modelo actualizado correctamente._"
    except Exception as e:
        return f"**Error** al actualizar: {e}"

def create_ui():
    with gr.Blocks(title="Privacy Filter - Local") as app:
        gr.Markdown("# Privacy Filter - Local\n*Deteccion de PII 100% local*")

        with gr.Tab("Texto"):
            with gr.Row():
                inp = gr.Textbox(
                    label="Texto a analizar",
                    lines=5,
                    placeholder="Mi nombre es Juan, email: juan@ejemplo.com, telefono: +34 612 345 678"
                )
                out = gr.Textbox(label="Resultado enmascarado", lines=5)
            btn = gr.Button("Detectar PII", variant="primary")
            info = gr.Markdown("_Escribe texto y haz clic en Detectar._")
            btn.click(fn=redact_text, inputs=inp, outputs=[out, info])
            gr.Examples(
                examples=[
                    "Hola, soy Maria Lopez. Mi email es maria@empresa.com y mi DNI es 12345678Z.",
                    "Contacta al +34 912 345 678 o envia email a ayuda@soporte.es",
                    "La reunion es el 15/03/2026. Cuenta: ES91 2100 0418 4502 0005 1332",
                ],
                inputs=inp
            )

        with gr.Tab("Archivos"):
            gr.Markdown("Sube archivos de texto o PDF para enmascarar PII.")
            finp = gr.File(
                label="Subir archivo",
                file_types=[".txt",".md",".csv",".json",".log",".py",".js",".xml",".html",".pdf",".docx"]
            )
            fbtn = gr.Button("Procesar Archivo", variant="primary")
            flegend = gr.Markdown()
            fpdf = gr.File(label="Archivo enmascarado (PDF/DOCX)", visible=True)
            fbtn.click(fn=redact_file, inputs=finp, outputs=[flegend, fpdf])

        with gr.Tab("Info"):
            gr.Markdown("""
            ## Categorias PII detectadas

            | Categoria | Descripcion |
            |-----------|-------------|
            | PERSON | Nombres de personas |
            | EMAIL | Direcciones email |
            | PHONE | Telefonos |
            | ADDRESS | Direcciones postales |
            | DATE | Fechas personales |
            | URL | Enlaces web |
            | ACCOUNT_NUMBER | Cuentas bancarias, tarjetas |
            | SECRET | Contrasenas, claves API |

            ## Formatos soportados

            - Texto: .txt, .md, .csv, .json, .log, .py, .js, .xml, .html
            - PDF: .pdf (devuelve PDF enmascarado)
            - DOCX: .docx (devuelve DOCX enmascarado)

            ## Seguridad
            
            - 100% local - nada se envia a internet
            - Modelo en tu PC
            - Licencia Apache 2.0
            """)
            update_btn = gr.Button("Actualizar modelo", variant="secondary")
            update_msg = gr.Markdown()
            update_btn.click(fn=update_model, outputs=update_msg)

    return app

if __name__ == "__main__":
    print("=" * 50)
    print("  Privacy Filter - Interfaz Local")
    print("=" * 50)
    print()
    print("El modelo se cargara la primera vez que uses Detectar.")
    print()
    print("Abre http://localhost:7860")
    print()

    app = create_ui()
    app.queue()
    app.launch(server_name="0.0.0.0", server_port=7860, share=False)
'@

    Set-Content -Path $appPath -Value $appContent -Encoding UTF8
    Write-OK "app_local.py creado"

    # Crear iniciar.bat
    $batPath = "$PROJECT_DIR\iniciar.bat"
    $batContent = @"
@echo off
title Privacy Filter - Local
color 0A

echo ========================================
echo   Privacy Filter - Local
echo ========================================
echo.

cd /d "%~dp0"

echo Iniciando servidor web...
echo Abre http://localhost:7860 en tu navegador
echo Presiona Ctrl+C para detener
echo.

python app_local.py

pause
"@
    Set-Content -Path $batPath -Value $batContent -Encoding ASCII
    Write-OK "iniciar.bat creado"
}

# ============================================================
#  MAIN
# ============================================================

function Main {
    Clear-Host

    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host "  OpenAI Privacy Filter - Instalador Completo" -ForegroundColor Cyan
    Write-Host "================================================================" -ForegroundColor Cyan

    $startTime = Get-Date

    # FASE 0: Diagnostico inicial
    Write-Step "FASE 0: DIAGNOSTICO DEL SISTEMA"

    # Comprobar Python
    $py = Test-PythonReal
    if ($py.Found) {
        Write-OK "Python encontrado: $($py.Version)"
    } else {
        Write-Warn "Python NO encontrado"
    }

    # Comprobar Git
    if (Test-CommandExists "git") {
        Write-OK "Git encontrado: $(git --version)"
    } else {
        Write-Warn "Git NO encontrado"
    }

    # Comprobar gestor de paquetes
    if (Test-CommandExists "winget") {
        Write-OK "winget disponible"
    } elseif (Test-CommandExists "choco") {
        Write-OK "chocolatey disponible"
    } else {
        Write-Warn "No hay gestor de paquetes (se usara descarga directa)"
    }

    # FASE 1: Python
    if (-not $py.Found) {
        if (-not (Install-Python)) {
            Write-Host "`n[FATAL] No se pudo instalar Python." -ForegroundColor Red
            Write-Host "Instalalo manualmente: https://www.python.org/downloads/" -ForegroundColor Yellow
            Write-Host "Marca 'Add Python to PATH'" -ForegroundColor Yellow
            exit 1
        }
    }

    # FASE 2: Git
    if (-not (Test-CommandExists "git")) {
        if (-not (Install-Git)) {
            Write-Host "`n[FATAL] No se pudo instalar Git." -ForegroundColor Red
            Write-Host "Instalalo manualmente: https://git-scm.com/download/win" -ForegroundColor Yellow
            exit 1
        }
    }

    # FASE 3: Repositorio
    if (-not (Clone-Repository)) {
        Write-Host "`n[FATAL] No se pudo obtener el repositorio." -ForegroundColor Red
        exit 1
    }

    # FASE 4: Dependencias
    if (-not (Install-Dependencies)) {
        Write-Host "`n[FATAL] No se pudieron instalar las dependencias." -ForegroundColor Red
        exit 1
    }

    # FASE 5: App
    New-AppLocal

    $elapsed = (Get-Date) - $startTime
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "  INSTALACION COMPLETADA ($($elapsed.Minutes)m $($elapsed.Seconds)s)" -ForegroundColor Green
    Write-Host "================================================================" -ForegroundColor Green

    # FASE 6: Ejecutar
    if (-not $NoRun) {
        Write-Host ""
        Write-Host "  Abre http://localhost:7860 en tu navegador" -ForegroundColor Cyan
        Write-Host "  Presiona Ctrl+C para detener" -ForegroundColor Yellow
        Write-Host ""

        Push-Location $PROJECT_DIR
        python app_local.py
        Pop-Location
    } else {
        Write-Host ""
        Write-Host "Para ejecutar:" -ForegroundColor Cyan
        Write-Host "  cd $PROJECT_DIR" -ForegroundColor White
        Write-Host "  python app_local.py" -ForegroundColor White
        Write-Host ""
    }
}

Main
