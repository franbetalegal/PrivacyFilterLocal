import { useRef, useState } from "react";
import {
  convertToMarkdown,
  downloadUrl,
  isAbort,
  type MarkdownOnlyResult,
} from "../api";
import Processing from "../components/Processing";

const ACCEPT =
  ".pdf,.docx,.doc,.pptx,.xlsx,.xls,.html,.htm,.csv,.json,.xml,.txt,.md";

export default function MarkdownTab() {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<MarkdownOnlyResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  function onCancel() {
    abortRef.current?.abort();
  }

  async function onConvert() {
    if (!file) {
      setError("Sube un archivo.");
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await convertToMarkdown(file, controller.signal);
      setResult(res);
    } catch (e) {
      if (!isAbort(e)) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }

  function onDownload(e: React.MouseEvent<HTMLAnchorElement>) {
    const ok = window.confirm(
      "AVISO: este archivo Markdown NO está anonimizado y puede contener " +
        "datos personales del documento original.\n\n" +
        "No lo pegue en una IA externa ni lo comparta fuera de su equipo sin " +
        "revisarlo antes.\n\n¿Descargar de todas formas?",
    );
    if (!ok) e.preventDefault();
  }

  return (
    <div className="tab-content">
      <p className="muted">
        Convierte cualquier documento (PDF, DOCX, PPTX, XLSX, HTML…) a Markdown
        sin pasar por la anonimización. Útil para tener una versión en formato
        limpio y con menos tokens.
      </p>

      <div className="warning md-warning">
        <strong>⚠ Este resultado NO está anonimizado.</strong>
        <br />
        El .md conserva todos los datos del original. No lo pegue en una IA
        externa ni lo comparta fuera del despacho sin revisar. Si necesita una
        versión anónima, use la pestaña <em>Archivos</em> con la opción
        «Convertir también a Markdown».
      </div>

      <input
        type="file"
        accept={ACCEPT}
        disabled={loading}
        onChange={(e) => {
          setFile(e.target.files?.[0] ?? null);
          setResult(null);
          setError(null);
        }}
      />

      <div className="row">
        <button
          className="btn primary"
          onClick={onConvert}
          disabled={loading || !file}
        >
          {loading ? "Convirtiendo…" : "Convertir a Markdown"}
        </button>
        {loading && (
          <button className="btn" onClick={onCancel}>
            Cancelar
          </button>
        )}
      </div>

      {loading && <Processing label="Convirtiendo a Markdown…" />}

      {error && <p className="error">Error: {error}</p>}

      {result && (
        <div className="result">
          <p>
            Convertido en <strong>{result.elapsed.toFixed(1)}s</strong> ·{" "}
            {result.char_count.toLocaleString()} caracteres.
          </p>
          <p>
            <a
              className="btn primary"
              href={downloadUrl(result.download_token)}
              download={result.download_name}
              onClick={onDownload}
            >
              ⬇ Descargar {result.download_name}
            </a>
          </p>
          <p className="muted">
            Al pulsar «Descargar» se le pedirá confirmar, porque el archivo
            contiene los datos originales sin anonimizar.
          </p>
        </div>
      )}
    </div>
  );
}
