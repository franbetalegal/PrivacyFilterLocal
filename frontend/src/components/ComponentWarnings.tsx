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
            No está disponible Tesseract, el motor de OCR. Un PDF sin capa de
            texto se extraerá vacío y no se detectará nada en él, lo que en
            pantalla se parece a un documento sin datos personales. Los
            documentos con texto seleccionable se procesan con normalidad.
          </p>
          <p className="muted">
            Esta instalación debería traerlo incluido, así que esto indica que
            algo le falta o no arranca. Descargue el diagnóstico desde la
            cabecera y envíelo para soporte, o vuelva a instalar la aplicación.
          </p>
        </div>
      </div>
    </div>
  );
}
