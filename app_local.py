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
