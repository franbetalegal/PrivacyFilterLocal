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
