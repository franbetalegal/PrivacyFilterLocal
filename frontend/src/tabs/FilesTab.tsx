import { useEffect, useState } from "react";
import { redactFile, getHealth, downloadUrl, type DetectedSpan } from "../api";
import SpanList from "../components/SpanList";
import Processing from "../components/Processing";

const ACCEPT = ".txt,.md,.csv,.json,.log,.py,.js,.xml,.html,.pdf,.docx";
const FIRST_RUN_HINT =
  "The first run loads the model and can take ~30s. After that it is fast.";

export default function FilesTab() {
  const [file, setFile] = useState<File | null>(null);
  const [spans, setSpans] = useState<DetectedSpan[]>([]);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [downloadToken, setDownloadToken] = useState<string | null>(null);
  const [downloadName, setDownloadName] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ran, setRan] = useState(false);
  const [modelReady, setModelReady] = useState(true);

  useEffect(() => {
    getHealth()
      .then((h) => setModelReady(h.model_loaded))
      .catch(() => setModelReady(true));
  }, []);

  async function onProcess() {
    if (!file) {
      setError("Upload a file.");
      return;
    }
    setLoading(true);
    setError(null);
    setDownloadToken(null);
    try {
      const res = await redactFile(file);
      setSpans(res.detected_spans);
      setElapsed(res.elapsed);
      setDownloadToken(res.download_token);
      setDownloadName(res.download_name);
      setRan(true);
      setModelReady(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="tab-content">
      <p className="muted">Upload a text, PDF or DOCX file to redact PII.</p>

      <input
        type="file"
        accept={ACCEPT}
        disabled={loading}
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
      />

      <div className="row">
        <button className="btn primary" onClick={onProcess} disabled={loading}>
          {loading ? "Processing…" : "Process File"}
        </button>
      </div>

      {loading && (
        <Processing
          label="Processing file…"
          hint={!modelReady ? FIRST_RUN_HINT : undefined}
        />
      )}

      {error && <p className="error">Error: {error}</p>}

      {!loading && ran && !error && (
        <div className="result">
          <p>
            Processed in <strong>{elapsed != null ? `${elapsed.toFixed(1)}s` : "—"}</strong>{" "}
            — <strong>{spans.length}</strong> entities detected
          </p>
          {spans.length > 0 ? (
            <SpanList spans={spans} />
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
