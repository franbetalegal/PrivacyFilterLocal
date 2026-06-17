import { DIAGNOSTICS_URL, type Health } from "../api";

/** Full-panel screen shown until the backend is ready (model download / errors). */
export default function PreparingScreen({ health }: { health: Health | null }) {
  // Backend reachable but the first-run model download failed.
  if (health?.error) {
    return (
      <div className="prepare">
        <h2 className="error">Could not prepare the model</h2>
        <p className="muted">{health.error}</p>
        <p className="muted">
          Check your internet connection and restart the app. If it keeps
          failing, download the diagnostics and send them for support.
        </p>
        <a className="btn primary" href={DIAGNOSTICS_URL} download>
          ⬇ Download diagnostics
        </a>
      </div>
    );
  }

  // Model is being downloaded (first run).
  if (health?.downloading) {
    const pct = Math.max(0, Math.min(100, health.download_pct || 0));
    return (
      <div className="prepare">
        <div className="processing-row">
          <span className="spinner" aria-hidden="true" />
          <span>Preparing the app — downloading the model (first run)…</span>
        </div>
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <p className="muted">{pct}% · this happens only once (~2.7 GB).</p>
      </div>
    );
  }

  // Backend not reachable yet (still starting).
  return (
    <div className="prepare">
      <div className="processing-row">
        <span className="spinner" aria-hidden="true" />
        <span>Connecting to the local server…</span>
      </div>
    </div>
  );
}
