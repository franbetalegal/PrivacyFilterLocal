import { useEffect, useRef, useState } from "react";
import {
  redactText,
  captureExample,
  getHealth,
  isAbort,
  type DetectedSpan,
  type Mode,
} from "../api";
import SpanList from "../components/SpanList";
import ModeSelector from "../components/ModeSelector";
import Processing from "../components/Processing";

const FIRST_RUN_HINT =
  "The first run loads the model and can take ~30s. After that it is fast.";

const EXAMPLES = [
  "Hi, I'm John Smith. My email is john.smith@example.com and my SSN is 123-45-6789.",
  "Call me at +1 555 987 6543 or email support@company.org",
  "The meeting is on 03/15/2026. Account: 4532-1234-5678-9012",
];

export default function TextTab() {
  const [text, setText] = useState("");
  const [redacted, setRedacted] = useState("");
  const [spans, setSpans] = useState<DetectedSpan[]>([]);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ran, setRan] = useState(false);
  const [modelReady, setModelReady] = useState(true);
  const [mode, setMode] = useState<Mode>("balanced");
  const [captureMsg, setCaptureMsg] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    getHealth()
      .then((h) => setModelReady(h.model_loaded))
      .catch(() => setModelReady(true));
  }, []);

  function onCancel() {
    abortRef.current?.abort();
  }

  async function onDetect() {
    if (!text.trim()) {
      setError("Enter some text.");
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    setCaptureMsg(null);
    try {
      const res = await redactText(text, mode, controller.signal);
      setRedacted(res.redacted_text);
      setSpans(res.detected_spans);
      setElapsed(res.elapsed);
      setRan(true);
      setModelReady(true);
    } catch (e) {
      if (isAbort(e)) {
        setError(null); // user cancelled; just reset
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }

  async function onSaveExample() {
    try {
      const res = await captureExample(text, spans);
      setCaptureMsg(
        res.added
          ? `Guardado para evaluación (${res.total} ejemplo(s) en total).`
          : res.reason === "duplicate"
            ? "Este texto ya estaba en el conjunto de evaluación."
            : "No se pudo guardar el ejemplo.",
      );
    } catch (e) {
      setCaptureMsg(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="tab-content">
      <div className="two-col">
        <div className="col">
          <label htmlFor="input">Text to analyze</label>
          <textarea
            id="input"
            rows={6}
            value={text}
            disabled={loading}
            placeholder="My name is John, email: john@example.com, phone: +1 555 123 4567"
            onChange={(e) => setText(e.target.value)}
          />
        </div>
        <div className="col">
          <label htmlFor="output">Redacted output</label>
          <textarea id="output" rows={6} value={redacted} readOnly />
        </div>
      </div>

      <ModeSelector value={mode} onChange={setMode} disabled={loading} />

      <div className="row">
        <button className="btn primary" onClick={onDetect} disabled={loading}>
          {loading ? "Detecting…" : "Detect PII"}
        </button>
        {loading && (
          <button className="btn" onClick={onCancel}>
            Cancel
          </button>
        )}
        <div className="examples">
          {EXAMPLES.map((ex, i) => (
            <button key={i} className="chip" onClick={() => setText(ex)}>
              Example {i + 1}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <Processing
          label="Detecting PII…"
          hint={!modelReady ? FIRST_RUN_HINT : undefined}
        />
      )}

      {error && <p className="error">Error: {error}</p>}

      {!loading && ran && !error && (
        <div className="result">
          {spans.length > 0 ? (
            <>
              <p>
                <strong>{spans.length} entities detected</strong>
                {elapsed != null ? ` (${elapsed.toFixed(1)}s)` : ""}
              </p>
              <SpanList spans={spans} />
              <p>
                <button
                  className="btn"
                  onClick={onSaveExample}
                  title="Guarda este texto y sus entidades como ejemplo para medir la precisión (se queda en tu equipo)."
                >
                  Guardar como ejemplo de evaluación
                </button>
              </p>
              {captureMsg && <p className="notice">{captureMsg}</p>}
            </>
          ) : (
            <p className="muted">
              No PII entities detected{elapsed != null ? ` (${elapsed.toFixed(1)}s)` : ""}.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
