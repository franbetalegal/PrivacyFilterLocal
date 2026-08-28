import type { Health } from "../api";

/** Avisos permanentes sobre capas de detección que no están operativas.
 *
 *  Sólo aparece lo que degrada el resultado sin impedir el uso: si falta el
 *  modelo de nombres, la aplicación ni siquiera llega hasta aquí. El caso vivo
 *  es Tesseract, porque sin él un PDF escaneado se extrae vacío y "no se ha
 *  detectado nada" se lee igual que un documento limpio. */
export default function ComponentWarnings({ health }: { health: Health | null }) {
  const ocr = health?.components?.ocr;
  if (!ocr || ocr.available) return null;

  return (
    <div className="banners">
      <div className="banner warning-banner" role="status">
        <div>
          <strong>Los documentos escaneados no se pueden leer.</strong>
          <p className="muted">
            Falta Tesseract, el motor de OCR. Un PDF sin capa de texto se
            extraerá vacío y no se detectará nada en él, lo que en pantalla se
            parece a un documento sin datos personales. Los documentos con
            texto seleccionable se procesan con normalidad.
          </p>
          <p className="muted">
            Para instalarlo: <code>brew install tesseract tesseract-lang</code>{" "}
            en macOS, <code>sudo apt install tesseract-ocr tesseract-ocr-spa</code>{" "}
            en Linux, o el instalador de UB Mannheim en Windows.
          </p>
        </div>
      </div>
    </div>
  );
}
