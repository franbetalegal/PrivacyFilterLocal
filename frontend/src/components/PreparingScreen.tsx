import { DIAGNOSTICS_URL, type Health } from "../api";

/** Nombre legible de cada modelo de nombres, para no enseñar el identificador. */
const NER_MODEL_LABEL: Record<string, string> = {
  es_core_news_lg: "castellano",
  ca_core_news_lg: "catalán",
};

function nerLabel(name: string | null): string {
  if (!name) return "de nombres";
  return NER_MODEL_LABEL[name] ? `de nombres (${NER_MODEL_LABEL[name]})` : `«${name}»`;
}

/** Full-panel screen shown until the backend is ready (component downloads,
 *  errors). Every detection component has to be in place before the app can be
 *  used: anonimizar sin el detector de nombres devuelve un documento que
 *  parece limpio y no lo está. */
export default function PreparingScreen({ health }: { health: Health | null }) {
  const ner = health?.components?.ner;

  // Backend reachable but the first-run model download failed.
  if (health?.error || ner?.error) {
    return (
      <div className="prepare">
        <h2 className="error">No se pudo preparar la aplicación</h2>
        <p className="muted">{health?.error || ner?.error}</p>
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

  // PII model being downloaded (first run).
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

  // NER models being downloaded (first run, or first run after updating from a
  // version that never shipped them).
  if (ner?.installing) {
    const pct = Math.max(0, Math.min(100, ner.install_pct || 0));
    return (
      <div className="prepare">
        <div className="processing-row">
          <span className="spinner" aria-hidden="true" />
          <span>
            Preparando la aplicación: descargando el modelo {nerLabel(ner.current)}…
          </span>
        </div>
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <p className="muted">
          {pct}% · solo ocurre una vez (~1,2 GB). Sin este modelo no se detectan
          los nombres escritos en mayúsculas.
        </p>
      </div>
    );
  }

  const warmup = health?.components?.warmup;

  // Models are on disk but could not be loaded or exercised.
  if (warmup?.error) {
    const memory = warmup.error === "memory";
    return (
      <div className="prepare">
        <h2 className="error">No se pudieron cargar los modelos de detección</h2>
        <p className="muted">
          {memory
            ? "El equipo no tiene memoria libre suficiente. Cierre otras " +
              "aplicaciones (navegadores con muchas pestañas, Outlook, Teams) y " +
              "vuelva a abrir la aplicación. Se necesitan unos 6 GB libres."
            : warmup.error.startsWith("vocabulary")
              ? "No se pudo descargar un archivo que el modelo necesita la " +
                "primera vez. Compruebe la conexión a internet (o el proxy de " +
                "la red) y vuelva a abrir la aplicación."
              : "Ha ocurrido un error al cargar los modelos. Vuelva a abrir la " +
                "aplicación; si se repite, descargue el diagnóstico y envíelo " +
                "a soporte."}
        </p>
        <a className="btn primary" href={DIAGNOSTICS_URL} download>
          ⬇ Descargar diagnóstico
        </a>
      </div>
    );
  }

  // Everything is downloaded; loading it into memory and running it once.
  if (warmup?.warming || (health && !warmup?.warm && !health.downloading)) {
    return (
      <div className="prepare">
        <div className="processing-row">
          <span className="spinner" aria-hidden="true" />
          <span>Cargando los modelos de detección en memoria…</span>
        </div>
        <p className="muted">
          Suele tardar entre 30 segundos y 2 minutos. Se hace al abrir la
          aplicación para que el primer documento no tenga que esperar.
        </p>
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
