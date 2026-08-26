/**
 * Spanish rendering of the backend's message codes.
 *
 * Project convention: the backend is written in English and never emits user
 * prose. It sends a code plus parameters; the Spanish sentence lives here. The
 * code list is `server/messages.py` — adding a message means touching both
 * files.
 *
 * A code with no entry below is rendered as the raw code. That is deliberate:
 * an untranslated message should look wrong so it gets caught in review rather
 * than shipping as a silently missing warning.
 */

/** What the backend sends: a code and, optionally, parameters for it. */
export interface MessageRef {
  code: string;
  params?: Record<string, unknown>;
}

type Renderer = (params: Record<string, unknown>) => string;

const RENDERERS: Record<string, Renderer> = {
  // --- warnings ---
  leak_detected: (p) => {
    const n = Number(p.count ?? 0);
    return n === 1
      ? 'Se detectó 1 fragmento de datos personales que no pudo eliminarse del documento anonimizado. Revíselo antes de compartirlo.'
      : `Se detectaron ${n} fragmentos de datos personales que no pudieron eliminarse del documento anonimizado. Revíselo antes de compartirlo.`;
  },
  verification_unavailable_scanned: () =>
    'No se pudo verificar el documento anonimizado: procede de un escaneado y la copia resultante no tiene capa de texto, así que no es posible comprobar automáticamente si algún dato sobrevive dentro de la imagen. Revíselo manualmente antes de compartirlo.',
  tokenizer_decode_mismatch: () =>
    'El texto de entrada no coincide exactamente con la reconstrucción interna del modelo, así que las posiciones detectadas provienen del texto reconstruido. Revise el resultado con especial atención.',

  // --- errors ---
  unsupported_file_type: (p) => `Tipo de archivo no admitido: ${p.ext}.`,
  pdf_unreadable: () => 'No se pudo leer el PDF.',
  docx_unreadable: () => 'No se pudo leer el DOCX.',
  apply_requires_pdf_or_docx: (p) =>
    `Aplicar cambios solo admite PDF o DOCX (se recibió ${p.ext}).`,
  invalid_spans_json: (p) => `Los fragmentos enviados no son válidos: ${p.error}`,
  file_not_found_or_expired: () =>
    'El archivo no existe o el enlace de descarga ha caducado.',
  markdown_unsupported_format: (p) =>
    `MarkItDown no admite este formato: ${p.ext}.`,
  markdown_no_text: () =>
    'MarkItDown no pudo extraer texto de este archivo.',
  term_not_found: () => 'Término no encontrado.',
  term_empty: () => 'El término no puede estar vacío.',
  invalid_regex: (p) => `Expresión regular no válida: ${p.error}`,
  gold_set_empty: () => 'El conjunto de referencia está vacío.',
};

/** Render one backend message in Spanish. */
export function renderMessage(ref: MessageRef): string {
  const renderer = RENDERERS[ref.code];
  return renderer ? renderer(ref.params ?? {}) : ref.code;
}

/** Render a list of warnings as one block of text, one per line. */
export function renderMessages(refs: MessageRef[] | null | undefined): string | null {
  if (!refs || refs.length === 0) return null;
  return refs.map(renderMessage).join('\n');
}

/**
 * Turn whatever an HTTP error carried into a Spanish sentence.
 *
 * The backend sends `{code, params}`, but a proxy, a crash or an older build
 * can still produce a plain string or nothing at all, so every shape has a
 * readable outcome.
 */
export function renderErrorDetail(detail: unknown, fallback: string): string {
  if (detail && typeof detail === 'object' && 'code' in detail) {
    return renderMessage(detail as MessageRef);
  }
  if (typeof detail === 'string' && detail.trim() !== '') {
    return detail;
  }
  return fallback;
}
