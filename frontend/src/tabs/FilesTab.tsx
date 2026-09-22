import { Fragment, useEffect, useMemo, useRef, useState } from "react";
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
import { renderMessages } from '../messages';

const ACCEPT = ".txt,.md,.csv,.json,.log,.py,.js,.xml,.html,.pdf,.docx";
const FIRST_RUN_HINT =
  "La primera ejecución carga el modelo y puede tardar unos 30 s. Después es rápida.";
const DOC_EXTS = new Set([".pdf", ".docx"]);

function ext(name: string | undefined | null): string {
  if (!name) return "";
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot).toLowerCase();
}

type JobStatus = "pending" | "processing" | "done" | "error" | "cancelled";

interface Job {
  file: File;
  status: JobStatus;
  spans: DetectedSpan[];
  selection: boolean[];
  elapsed?: number;
  timings?: StageTimings;
  downloadToken?: string | null;
  downloadName?: string | null;
  markdownToken?: string | null;
  markdownName?: string | null;
  warning?: string | null;
  error?: string;
}

function newJob(file: File): Job {
  return { file, status: "pending", spans: [], selection: [] };
}

export default function FilesTab() {
  // One-file mode = jobs.length === 1 (human-in-the-loop review). N-file mode
  // = queue with no per-file review; the two share this single state array
  // so the flow scales up cleanly.
  const [jobs, setJobs] = useState<Job[]>([]);
  const [modelReady, setModelReady] = useState(true);
  const [mode, setMode] = useState<Mode>("balanced");
  const [alsoMarkdown, setAlsoMarkdown] = useState(false);
  const [saveExample, setSaveExample] = useState(false);
  const [captureMsg, setCaptureMsg] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false); // true while any job is running
  const [queueStopRequested, setQueueStopRequested] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    getHealth()
      .then((h) => setModelReady(h.model_loaded))
      .catch(() => setModelReady(true));
  }, []);

  const singleMode = jobs.length === 1;
  const single = singleMode ? jobs[0] : null;
  const isReviewable =
    single !== null && DOC_EXTS.has(ext(single.file.name));
  const keptSpans = useMemo(
    () => (single ? single.spans.filter((_, i) => single.selection[i]) : []),
    [single],
  );

  function updateJob(index: number, patch: Partial<Job>) {
    setJobs((cur) =>
      cur.map((j, i) => (i === index ? { ...j, ...patch } : j)),
    );
  }

  function onCancel() {
    setQueueStopRequested(true);
    abortRef.current?.abort();
  }

  function toggleSpan(idx: number) {
    if (!single) return;
    const next = single.selection.map((v, i) => (i === idx ? !v : v));
    updateJob(0, { selection: next });
  }

  function toggleAll(checked: boolean) {
    if (!single) return;
    updateJob(0, { selection: single.selection.map(() => checked) });
  }

  function onFilesChosen(files: FileList | null) {
    setGlobalError(null);
    setCaptureMsg(null);
    setQueueStopRequested(false);
    setJobs(files ? Array.from(files).map(newJob) : []);
  }

  async function runDetection(index: number): Promise<boolean> {
    const job = jobs[index];
    if (!job) return false;
    const controller = new AbortController();
    abortRef.current = controller;
    updateJob(index, { status: "processing", error: undefined });
    try {
      const t0 = performance.now();
      const res = await redactFile(
        job.file, mode, controller.signal, alsoMarkdown,
      );
      updateJob(index, {
        status: "done",
        spans: res.detected_spans,
        selection: res.detected_spans.map(() => true),
        elapsed: Math.max(res.elapsed ?? 0, (performance.now() - t0) / 1000),
        timings: res.timings,
        downloadToken: res.download_token,
        downloadName: res.download_name,
        markdownToken: res.markdown_token ?? null,
        markdownName: res.markdown_name ?? null,
        warning: renderMessages(res.warnings),
      });
      setModelReady(true);
      return true;
    } catch (e) {
      if (isAbort(e)) {
        updateJob(index, { status: "cancelled" });
      } else {
        updateJob(index, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
      return false;
    } finally {
      abortRef.current = null;
    }
  }

  async function onProcessOne() {
    if (!jobs.length) {
      setGlobalError("Suba al menos un archivo.");
      return;
    }
    setBusy(true);
    try {
      await runDetection(0);
    } finally {
      setBusy(false);
    }
  }

  async function onProcessQueue() {
    if (!jobs.length) return;
    setBusy(true);
    setQueueStopRequested(false);
    try {
      // Backend serializes on a single-worker ThreadPoolExecutor anyway, so
      // sequential submission is not just simpler — it also lines up progress
      // with actual work, and one cancel stops the current file immediately.
      for (let i = 0; i < jobs.length; i++) {
        if (queueStopRequested) break;
        if (jobs[i].status === "done") continue;
        await runDetection(i);
      }
    } finally {
      setBusy(false);
    }
  }

  async function onReDetect() {
    if (!single) return;
    setBusy(true);
    try {
      await runDetection(0);
    } finally {
      setBusy(false);
    }
  }

  async function onApplySelection() {
    if (!single) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setCaptureMsg(null);
    try {
      const t0 = performance.now();
      const res = await applyRedaction(
        single.file, keptSpans, controller.signal, saveExample, alsoMarkdown,
      );
      updateJob(0, {
        elapsed: Math.max(res.elapsed ?? 0, (performance.now() - t0) / 1000),
        timings: res.timings,
        downloadToken: res.download_token,
        downloadName: res.download_name,
        markdownToken: res.markdown_token ?? null,
        markdownName: res.markdown_name ?? null,
        warning: renderMessages(res.warnings),
      });
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
      if (!isAbort(e)) {
        setGlobalError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  const queueMode = jobs.length >= 2;
  const queueDone = queueMode && jobs.every((j) => j.status !== "pending" && j.status !== "processing");
  const queueProgress = queueMode
    ? jobs.filter((j) => j.status === "done").length
    : 0;

  return (
    <div className="tab-content">
      <p className="muted">
        Suba uno o varios archivos de texto, PDF o DOCX. Con un archivo puede
        revisar las entidades detectadas y editarlas antes de la copia final.
        Con varios archivos, la anonimización se ejecuta en cola sin revisión
        intermedia y podrá descargar cada resultado según termine.
      </p>

      <input
        type="file"
        accept={ACCEPT}
        multiple
        disabled={busy}
        onChange={(e) => onFilesChosen(e.target.files)}
      />

      <ModeSelector value={mode} onChange={setMode} disabled={busy} />

      <label
        className="save-example"
        title="Convierte el documento anonimizado a Markdown para poder pegarlo en una IA con menos tokens y con la estructura (títulos, listas y tablas) preservada."
      >
        <input
          type="checkbox"
          checked={alsoMarkdown}
          disabled={busy}
          onChange={(e) => setAlsoMarkdown(e.target.checked)}
        />
        Convertir también a Markdown (para pegar en una IA)
      </label>

      <div className="row">
        {singleMode && (
          <button
            className="btn primary"
            onClick={onProcessOne}
            disabled={busy || jobs[0].status === "done"}
          >
            {busy ? "Procesando…" : "Procesar archivo"}
          </button>
        )}
        {queueMode && (
          <button
            className="btn primary"
            onClick={onProcessQueue}
            disabled={busy || queueDone}
          >
            {busy
              ? `Procesando ${queueProgress + 1}/${jobs.length}…`
              : queueDone
                ? "Cola completada"
                : `Procesar cola (${jobs.length} archivo(s))`}
          </button>
        )}
        {busy && (
          <button className="btn" onClick={onCancel}>
            Cancelar
          </button>
        )}
      </div>

      {busy && singleMode && (
        <Processing
          label="Procesando archivo…"
          hint={!modelReady ? FIRST_RUN_HINT : undefined}
        />
      )}

      {globalError && <p className="error">Error: {globalError}</p>}

      {queueMode && <QueueTable jobs={jobs} />}

      {singleMode && jobs[0].status === "done" && (
        <SingleResult
          job={jobs[0]}
          isReviewable={isReviewable}
          keptSpans={keptSpans}
          busy={busy}
          onToggleSpan={toggleSpan}
          onToggleAll={toggleAll}
          onApplySelection={onApplySelection}
          onReDetect={onReDetect}
          saveExample={saveExample}
          onSaveExampleChange={setSaveExample}
          captureMsg={captureMsg}
        />
      )}

      {singleMode && jobs[0].status === "error" && (
        <p className="error">Error: {jobs[0].error}</p>
      )}
    </div>
  );
}

// --- Queue table (N ≥ 2) ---------------------------------------------------

function QueueTable({ jobs }: { jobs: Job[] }) {
  const doneCount = jobs.filter((j) => j.status === "done").length;
  const anyError = jobs.some((j) => j.status === "error");
  return (
    <div className="result">
      <p>
        <strong>{doneCount}/{jobs.length}</strong> archivo(s) completado(s)
        {anyError && <span className="error"> · errores en la cola</span>}
      </p>
      <table className="dict-table queue-table">
        <thead>
          <tr>
            <th>Archivo</th>
            <th>Estado</th>
            <th>Entidades</th>
            <th>Tiempo</th>
            <th>Descargar</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j, i) => (
            // A single-file review row + a details row beneath it that lists
            // the actual PII the pipeline replaced. The details row is
            // rendered only when there are spans to show, so `<td colSpan>`
            // stays honest about the table shape.
            <Fragment key={i}>
              <tr>
                <td className="mono">{j.file.name}</td>
                <td><StatusBadge status={j.status} /></td>
                <td className="muted">
                  {j.status === "done" ? j.spans.length : "—"}
                </td>
                <td className="muted">
                  {j.elapsed != null ? `${j.elapsed.toFixed(1)}s` : "—"}
                </td>
                <td>
                  {j.downloadToken ? (
                    <a
                      className="btn small"
                      href={downloadUrl(j.downloadToken)}
                      download={j.downloadName ?? undefined}
                    >
                      ⬇ {j.downloadName ?? "archivo"}
                    </a>
                  ) : j.status === "error" ? (
                    <span className="error small">{j.error}</span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                  {j.markdownToken && (
                    <>
                      {" "}
                      <a
                        className="btn small"
                        href={downloadUrl(j.markdownToken)}
                        download={j.markdownName ?? undefined}
                      >
                        ⬇ .md
                      </a>
                    </>
                  )}
                </td>
              </tr>
              {j.status === "done" && j.spans.length > 0 && (
                <tr className="queue-spans-row">
                  <td colSpan={5}>
                    <SpanList spans={j.spans} />
                  </td>
                </tr>
              )}
              {j.warning && (
                <tr className="queue-spans-row">
                  <td colSpan={5}>
                    <p className="warning">⚠ {j.warning}</p>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        Cada descarga es de un solo uso: si necesita el archivo más de una
        vez, guárdelo la primera. Reprocesar la cola vuelve a generar los
        enlaces.
      </p>
    </div>
  );
}

function StatusBadge({ status }: { status: JobStatus }) {
  const label: Record<JobStatus, string> = {
    pending: "En espera",
    processing: "Procesando…",
    done: "Hecho",
    error: "Error",
    cancelled: "Cancelado",
  };
  return <span className={`tag status-${status}`}>{label[status]}</span>;
}

// --- Single-file review (N === 1) -----------------------------------------

interface SingleResultProps {
  job: Job;
  isReviewable: boolean;
  keptSpans: DetectedSpan[];
  busy: boolean;
  onToggleSpan: (i: number) => void;
  onToggleAll: (checked: boolean) => void;
  onApplySelection: () => void;
  onReDetect: () => void;
  saveExample: boolean;
  onSaveExampleChange: (v: boolean) => void;
  captureMsg: string | null;
}

function SingleResult(p: SingleResultProps) {
  const { job } = p;
  return (
    <div className="result">
      <p>
        Procesado en{" "}
        <strong>{job.elapsed != null ? `${job.elapsed.toFixed(1)}s` : "—"}</strong>{" "}
        — <strong>{job.spans.length}</strong> entidad(es) detectada(s)
      </p>
      {job.timings && (
        <p className="muted timings">
          OCR/extracción {job.timings.extract.toFixed(1)}s · detección{" "}
          {job.timings.detect.toFixed(1)}s · anonimización{" "}
          {job.timings.redact.toFixed(1)}s
          {job.timings.verify > 0 &&
            ` · verificación ${job.timings.verify.toFixed(1)}s`}
        </p>
      )}
      {job.warning && <p className="warning">⚠ {job.warning}</p>}
      {job.spans.length > 0 ? (
        <>
          <SpanList
            spans={job.spans}
            selection={p.isReviewable ? job.selection : undefined}
            onToggle={p.isReviewable ? p.onToggleSpan : undefined}
            onToggleAll={p.isReviewable ? p.onToggleAll : undefined}
          />
          {p.isReviewable && (
            <div className="review-actions">
              <button
                className="btn"
                onClick={p.onApplySelection}
                disabled={p.busy}
                title="Aplica al archivo solo las entidades que ha marcado. No vuelve a ejecutar la detección, así que los cambios recientes en el diccionario NO se aplican por esta vía."
              >
                ↻ Regenerar con la selección ({p.keptSpans.length}/{job.spans.length})
              </button>
              <button
                className="btn"
                onClick={p.onReDetect}
                disabled={p.busy}
                title="Vuelve a ejecutar la detección sobre el mismo archivo. Úselo tras añadir o modificar términos del diccionario."
              >
                🔍 Volver a detectar (aplica el diccionario actual)
              </button>
              <label
                className="save-example"
                title="Guarda el texto y sus entidades corregidas como ejemplo para medir la precisión (se queda en su equipo)."
              >
                <input
                  type="checkbox"
                  checked={p.saveExample}
                  disabled={p.busy}
                  onChange={(e) => p.onSaveExampleChange(e.target.checked)}
                />
                Guardar como ejemplo de evaluación
              </label>
              {p.captureMsg && <p className="notice">{p.captureMsg}</p>}
              <p className="muted small">
                Si acaba de añadir o modificar términos en el diccionario,
                pulse «Volver a detectar» para aplicarlos. «Regenerar con la
                selección» solo re-empaqueta el archivo con las entidades ya
                marcadas y no vuelve a mirar el diccionario.
              </p>
            </div>
          )}
        </>
      ) : (
        <p className="muted">No se han detectado datos personales.</p>
      )}
      {job.downloadToken && (
        <p className="row">
          <a
            className="btn"
            href={downloadUrl(job.downloadToken)}
            download={job.downloadName ?? undefined}
          >
            ⬇ Descargar {job.downloadName ?? "archivo anonimizado"}
          </a>
          {job.markdownToken && (
            <a
              className="btn"
              href={downloadUrl(job.markdownToken)}
              download={job.markdownName ?? undefined}
              title="Documento anonimizado como Markdown, listo para pegar en una IA."
            >
              ⬇ Descargar .md {job.markdownName ? `(${job.markdownName})` : ""}
            </a>
          )}
        </p>
      )}
    </div>
  );
}
