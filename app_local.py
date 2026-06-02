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
    except Exception as e:
        print(f"[PDF ERROR] {e}")
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
            text = path.read_text(encoding="utf-8", errors="replace")

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
        return f"**Error:** {e}"


# ============================================================
#  APP UPDATE FUNCTIONS
# ============================================================
#  APP UPDATE FUNCTIONS
# ============================================================

_app_update_info = None

def _check_app_update_background():
    """Background thread that checks for app updates."""
    global _app_update_info
    try:
        from app_update import check_for_app_update
        _app_update_info = check_for_app_update()
        if _app_update_info.update_available:
            print(f"[UPDATE] App update available: v{_app_update_info.current_version} -> v{_app_update_info.latest_version}")
    except Exception:
        pass


# ============================================================
#  MODEL UPDATE FUNCTIONS
# ============================================================

_model_update_info = None

def _check_model_update_background():
    """Background thread that checks for model updates."""
    global _model_update_info
    try:
        from model_update import check_for_model_update
        _model_update_info = check_for_model_update()
        if _model_update_info.update_available:
            print(f"[UPDATE] Model update available: {_model_update_info.current_date} -> {_model_update_info.latest_date}")
    except Exception:
        pass

def install_app_update(progress=gr.Progress()):
    """Download and install the app update."""
    global _app_update_info
    
    if not _app_update_info or not _app_update_info.update_available:
        return "_No update available._"
    
    if not _app_update_info.download_url:
        return "_No download URL found for this update._"
    
    try:
        from app_update import download_and_install_update, restart_app
        
        def update_progress(message, pct):
            progress((pct, 1.0), desc=message)
        
        success, message = download_and_install_update(
            _app_update_info.download_url,
            progress_callback=update_progress,
        )
        
        if success:
            progress((1.0, 1.0), desc="Done!")
            # Schedule restart after 2 seconds
            threading.Timer(2.0, restart_app).start()
            return f"**{message}**\n\nRestarting in 2 seconds..."
        else:
            return f"**Update failed:** {message}\n\nPlease update manually:\n```\ncd {Path(__file__).parent}\ngit pull\npip install -e .\n```"
    
    except Exception as e:
        return f"**Error:** {e}"


def install_model_update(progress=gr.Progress()):
    """Download and install the model update."""
    global _model_update_info

    if not _model_update_info or not _model_update_info.update_available:
        return "_No model update available._"

    try:
        from model_update import download_model_update

        def update_progress(message, pct):
            progress((pct, 1.0), desc=message)

        success, message = download_model_update(
            progress_callback=update_progress,
        )

        if success:
            # Reload the model
            global _model
            _model = None
            get_model()

            progress((1.0, 1.0), desc="Done!")
            return f"**{message}**"
        else:
            return f"**Update failed:** {message}"

    except Exception as e:
        return f"**Error:** {e}"


def create_ui():
    with gr.Blocks(title="Privacy Filter - Local") as app:
        # Read current version
        try:
            from app_update import get_local_version
            current_version = get_local_version()
        except Exception:
            current_version = "unknown"
        
        # Update banner (initially hidden)
        with gr.Row():
            update_banner = gr.Markdown(
                value="",
                visible=False,
            )
        
        with gr.Row():
            update_btn = gr.Button(
                "Update now",
                variant="primary",
                visible=False,
                scale=1,
            )
            later_btn = gr.Button(
                "Later",
                variant="secondary",
                visible=False,
                scale=1,
            )
        
        update_msg = gr.Markdown()
        
        update_btn.click(
            fn=install_app_update,
            outputs=update_msg,
        )
        
        later_btn.click(
            fn=lambda: (
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
            ),
            outputs=[update_banner, update_btn, later_btn],
        )

        # Model update banner (initially hidden)
        with gr.Row():
            model_update_banner = gr.Markdown(
                value="",
                visible=False,
            )

        with gr.Row():
            model_update_btn = gr.Button(
                "Update model",
                variant="primary",
                visible=False,
                scale=1,
            )
            model_later_btn = gr.Button(
                "Later",
                variant="secondary",
                visible=False,
                scale=1,
            )

        model_update_msg = gr.Markdown()

        model_update_btn.click(
            fn=install_model_update,
            outputs=model_update_msg,
        )

        model_later_btn.click(
            fn=lambda: (
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=False),
            ),
            outputs=[model_update_banner, model_update_btn, model_later_btn],
        )

        gr.Markdown(f"# Privacy Filter - Local\n*100% local PII detection* | v{current_version}")

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
                    "Hi, I'm John Smith. My email is john.smith@example.com and my SSN is 123-45-6789.",
                    "Call me at +1 555 987 6543 or email support@company.org",
                    "The meeting is on 03/15/2026. Account: 4532-1234-5678-9012",
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
            manual_update_btn.click(fn=install_model_update, outputs=manual_update_msg)

        # Load update check on app start
        def check_and_update_banner():
            """Check for updates and return banner content."""
            global _app_update_info
            global _model_update_info
            
            # Wait for background checks to complete
            for _ in range(50):  # Wait up to 5 seconds
                if _app_update_info is not None and _model_update_info is not None:
                    break
                time.sleep(0.1)
            
            # App update banner
            if _app_update_info is None or not _app_update_info.update_available:
                app_banner_update = (
                    gr.update(visible=False),
                    gr.update(visible=False),
                    gr.update(visible=False),
                )
            else:
                # Format changelog
                changelog = _app_update_info.changelog
                if changelog:
                    # Trim long changelogs
                    lines = changelog.split("\n")
                    if len(lines) > 20:
                        changelog = "\n".join(lines[:20]) + "\n\n..."
                
                date_str = f" ({_app_update_info.published_date})" if _app_update_info.published_date else ""
                banner_text = f"""### A new version is available: v{_app_update_info.current_version} \u2192 v{_app_update_info.latest_version}{date_str}

**What's New:**

{changelog if changelog else "No changelog available."}
"""
                
                app_banner_update = (
                    gr.update(value=banner_text, visible=True),
                    gr.update(visible=True),
                    gr.update(visible=True),
                )
            
            # Model update banner
            if _model_update_info is None or not _model_update_info.update_available:
                model_banner_update = (
                    gr.update(visible=False),
                    gr.update(visible=False),
                    gr.update(visible=False),
                )
            else:
                current_date = _model_update_info.current_date or "unknown"
                latest_date = _model_update_info.latest_date or "unknown"
                model_banner_text = f"""### New PII model update available

Current: {current_date} \u2192 Latest: {latest_date}
"""
                model_banner_update = (
                    gr.update(value=model_banner_text, visible=True),
                    gr.update(visible=True),
                    gr.update(visible=True),
                )
            
            return app_banner_update + model_banner_update
        
        app.load(
            fn=check_and_update_banner,
            outputs=[update_banner, update_btn, later_btn, model_update_banner, model_update_btn, model_later_btn],
        )

    return app


def _start_update_check(app):
    """Start background update checks after a short delay."""
    threading.Thread(target=_check_app_update_background, daemon=True).start()
    threading.Thread(target=_check_model_update_background, daemon=True).start()


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
