import { useEffect, useMemo, useRef, useState } from "react";
import {
  redactFile,
  applyRedaction,
  getHealth,
  isAbort,
  downloadUrl,
  type DetectedSpan,
  type Mode,
  type StageTimings,
} from "../api";
import SpanList from "../components/SpanList";
import ModeSelector from "../components/ModeSelector";
import Processing from "../components/Processing";

const ACCEPT = ".txt,.md,.csv,.json,.log,.py,.js,.xml,.html,.pdf,.docx";
const FIRST_RUN_HINT =
  "The first run loads the model and can take ~30s. After that it is fast.";
const DOC_EXTS = new Set([".pdf", ".docx"]);

function ext(name: string | undefined | null): string {
  if (!name) return "";
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot).toLowerCase();
}

export default function FilesTab() {
  const [file, setFile] = useState<File | null>(null);
  const [spans, setSpans] = useState<DetectedSpan[]>([]);
  // Human-in-the-loop selection: one boolean per span, in order. Kept in
  // parallel with ``spans`` — resetting spans (a new detect run) resets this.
  const [selection, setSelection] = useState<boolean[]>([]);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [timings, setTimings] = useState<StageTimings | null>(null);
  const [downloadToken, setDownloadToken] = useState<string | null>(null);
  const [downloadName, setDownloadName] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ran, setRan] = useState(false);
  const [modelReady, setModelReady] = useState(true);
  const [mode, setMode] = useState<Mode>("balanced");
  const [saveExample, setSaveExample] = useState(false);
  const [captureMsg, setCaptureMsg] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    getHealth()
      .then((h) => setModelReady(h.model_loaded))
      .catch(() => setModelReady(true));
  }, []);

  const isReviewable = file !== null && DOC_EXTS.has(ext(file.name));
  const keptSpans = useMemo(
    () => spans.filter((_, i) => selection[i]),
    [spans, selection],
  );

  function onCancel() {
    abortRef.current?.abort();
  }

  function toggleSpan(index: number) {
    setSelection((cur) => cur.map((v, i) => (i === index ? !v : v)));
  }

  function toggleAll(checked: boolean) {
    setSelection((cur) => cur.map(() => checked));
  }

  async function onProcess() {
    if (!file) {
      setError("Upload a file.");
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    setDownloadToken(null);
    setWarning(null);
    try {
      const t0 = performance.now();
      const res = await redactFile(file, mode, controller.signal);
      setSpans(res.detected_spans);
      setSelection(res.detected_spans.map(() => true));
      // Guard against the server omitting a stage: the wall time the user
      // actually waited is the floor.
      setElapsed(Math.max(res.elapsed ?? 0, (performance.now() - t0) / 1000));
      setTimings(res.timings ?? null);
      setDownloadToken(res.download_token);
      setDownloadName(res.download_name);
      setWarning(res.warning ?? null);
      setRan(true);
      setModelReady(true);
    } catch (e) {
      if (isAbort(e)) {
        setError(null);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }

  async function onApplySelection() {
    if (!file) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setApplying(true);
    setError(null);
    setDownloadToken(null);
    setWarning(null);
    setCaptureMsg(null);
    try {
      const t0 = performance.now();
      const res = await applyRedaction(
        file, keptSpans, controller.signal, saveExample,
      );
      setElapsed(Math.max(res.elapsed ?? 0, (performance.now() - t0) / 1000));
      setTimings(res.timings ?? null);
      setDownloadToken(res.download_token);
      setDownloadName(res.download_name);
      setWarning(res.warning ?? null);
      if (saveExample && res.captured) {
        setCaptureMsg(
          res.captured.added
            ? `Guardado para evaluación (${res.captured.total} ejemplo(s) en total).`
            : res.captured.reason === "duplicate"
              ? "Este documento ya estaba en el conjunto de evaluación."
              : "No se pudo guardar el ejemplo.",
        );
      }
    } catch (e) {
      if (isAbort(e)) {
        setError(null);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setApplying(false);
      abortRef.current = null;
    }
  }

  const busy = loading || applying;
  return (
    <div className="tab-content">
      <p className="muted">
        Upload a text, PDF or DOCX file to redact PII. For PDF/DOCX you can
        review the detected entities and un-check any you want to keep before
        generating the final redacted copy.
      </p>

      <input
        type="file"
        accept={ACCEPT}
        disabled={busy}
        onChange={(e) => {
          setFile(e.target.files?.[0] ?? null);
          setRan(false);
          setSpans([]);
          setSelection([]);
          setDownloadToken(null);
          setWarning(null);
          setElapsed(null);
          setTimings(null);
        }}
      />

      <ModeSelector value={mode} onChange={setMode} disabled={busy} />

      <div className="row">
        <button className="btn primary" onClick={onProcess} disabled={busy}>
          {loading ? "Processing…" : "Process File"}
        </button>
        {busy && (
          <button className="btn" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>

      {loading && (
        <Processing
          label="Processing file…"
          hint={!modelReady ? FIRST_RUN_HINT : undefined}
        />
      )}
      {applying && <Processing label="Regenerando con tu selección…" />}

      {error && <p className="error">Error: {error}</p>}

      {!loading && ran && !error && (
        <div className="result">
          <p>
            Processed in <strong>{elapsed != null ? `${elapsed.toFixed(1)}s` : "—"}</strong>{" "}
            — <strong>{spans.length}</strong> entities detected
          </p>
          {timings && (
            <p className="muted timings">
              OCR/extracción {timings.extract.toFixed(1)}s · detección{" "}
              {timings.detect.toFixed(1)}s · redacción {timings.redact.toFixed(1)}s
              {timings.verify > 0 && ` · verificación ${timings.verify.toFixed(1)}s`}
            </p>
          )}
          {warning && <p className="warning">⚠ {warning}</p>}
          {spans.length > 0 ? (
            <>
              <SpanList
                spans={spans}
                selection={isReviewable ? selection : undefined}
                onToggle={isReviewable ? toggleSpan : undefined}
                onToggleAll={isReviewable ? toggleAll : undefined}
              />
              {isReviewable && (
                <div className="review-actions">
                  <button
                    className="btn"
                    onClick={onApplySelection}
                    disabled={busy}
                    title="Regenera el archivo aplicando solo las entidades marcadas."
                  >
                    ↻ Regenerar con mi selección ({keptSpans.length}/{spans.length})
                  </button>
                  <label
                    className="save-example"
                    title="Guarda el texto y tus entidades corregidas como ejemplo para medir la precisión (se queda en tu equipo)."
                  >
                    <input
                      type="checkbox"
                      checked={saveExample}
                      disabled={busy}
                      onChange={(e) => setSaveExample(e.target.checked)}
                    />
                    Guardar como ejemplo de evaluación
                  </label>
                  {captureMsg && <p className="notice">{captureMsg}</p>}
                </div>
              )}
            </>
          ) : (
            <p className="muted">No PII entities detected.</p>
          )}
          {downloadToken && (
            <p>
              <a
                className="btn"
                href={downloadUrl(downloadToken)}
                download={downloadName ?? undefined}
              >
                ⬇ Download {downloadName ?? "redacted file"}
              </a>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
