import { DIAGNOSTICS_URL, type Health } from "../api";

/** Full-panel screen shown until the backend is ready (model download / errors). */
export default function PreparingScreen({ health }: { health: Health | null }) {
  // Backend reachable but the first-run model download failed.
  if (health?.error) {
    return (
      <div className="prepare">
        <h2 className="error">No se pudo preparar el modelo</h2>
        <p className="muted">{health.error}</p>
        <p className="muted">
          Compruebe la conexión a internet y reinicie la aplicación. Si el
          problema persiste, descargue el diagnóstico y envíelo para soporte.
        </p>
        <a className="btn primary" href={DIAGNOSTICS_URL} download>
          ⬇ Descargar diagnóstico
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
          <span>
            Preparando la aplicación: descargando el modelo (primera
            ejecución)…
          </span>
        </div>
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <p className="muted">{pct}% · solo ocurre una vez (~2,7 GB).</p>
      </div>
    );
  }

  // Backend not reachable yet (still starting).
  return (
    <div className="prepare">
      <div className="processing-row">
        <span className="spinner" aria-hidden="true" />
        <span>Conectando con el servidor local…</span>
      </div>
    </div>
  );
}
