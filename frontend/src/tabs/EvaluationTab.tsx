import { useEffect, useState } from "react";
import {
  getDatasetStats,
  evaluateDataset,
  MODES,
  MODE_LABEL,
  type DatasetStats,
  type EvalReport,
  type Mode,
} from "../api";

function pct(x: number): string {
  return `${(x * 100).toFixed(1)}%`;
}

export default function EvaluationTab() {
  const [stats, setStats] = useState<DatasetStats | null>(null);
  const [report, setReport] = useState<EvalReport | null>(null);
  const [mode, setMode] = useState<Mode>("balanced");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refreshStats() {
    try {
      setStats(await getDatasetStats());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    refreshStats();
  }, []);

  async function onRun() {
    setRunning(true);
    setError(null);
    setReport(null);
    try {
      setReport(await evaluateDataset(mode));
      await refreshStats();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  const empty = stats != null && stats.examples === 0;

  return (
    <div className="tab-content">
      <p className="muted">
        Mide la calidad de la detección sobre el conjunto de ejemplos que se van
        guardando desde la revisión (pestañas Texto y Archivos). Cuantos más
        ejemplos, más fiable es la medida. Todo se calcula en su equipo.
      </p>

      <div className="eval-stats">
        {stats ? (
          <>
            <span className="tag">{stats.examples} ejemplo(s)</span>{" "}
            <span className="tag">{stats.spans} entidad(es) etiquetadas</span>
            {Object.keys(stats.by_label).length > 0 && (
              <span className="muted">
                {" "}
                ·{" "}
                {Object.entries(stats.by_label)
                  .map(([l, n]) => `${l}: ${n}`)
                  .join(" · ")}
              </span>
            )}
          </>
        ) : (
          <span className="muted">Cargando…</span>
        )}
      </div>

      {empty ? (
        <p className="notice">
          Aún no hay ejemplos guardados. Vaya a Texto o Archivos, revise una
          detección y pulse «Guardar como ejemplo de evaluación».
        </p>
      ) : (
        <div className="row eval-controls">
          <label>
            Modo:{" "}
            <select
              value={mode}
              disabled={running}
              onChange={(e) => setMode(e.target.value as Mode)}
            >
              {MODES.map((m) => (
                <option key={m} value={m}>
                  {MODE_LABEL[m]}
                </option>
              ))}
            </select>
          </label>
          <button className="btn primary" onClick={onRun} disabled={running}>
            {running ? "Evaluando…" : "Evaluar ahora"}
          </button>
        </div>
      )}

      {error && <p className="error">Error: {error}</p>}

      {report && report.examples > 0 && (
        <div className="result">
          <div className="eval-headline">
            <Metric label="Precisión" value={pct(report.overall.precision)} />
            <Metric label="Cobertura (recall)" value={pct(report.overall.recall)} />
            <Metric label="F1" value={pct(report.overall.f1)} />
          </div>
          <p className="muted">
            {report.examples} ejemplo(s) · {report.gold_spans} entidad(es) de
            referencia · {report.pred_spans} detectada(s) · modo {report.mode}.
            Coincidencia por solapamiento; con límites exactos:{" "}
            {pct(report.exact.f1)} de F1.
          </p>

          <table className="dict-table">
            <thead>
              <tr>
                <th>Etiqueta</th>
                <th>Precisión</th>
                <th>Cobertura</th>
                <th>F1</th>
                <th>Ref.</th>
                <th>Det.</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(report.by_label).map(([label, m]) => (
                <tr key={label}>
                  <td>
                    <span className="tag">[{label}]</span>
                  </td>
                  <td>{pct(m.precision)}</td>
                  <td>{pct(m.recall)}</td>
                  <td>{pct(m.f1)}</td>
                  <td className="muted">{m.gold}</td>
                  <td className="muted">{m.pred}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {report.leak_count > 0 ? (
            <div className="warning">
              ⚠ {report.leak_count} fuga(s): datos personales de referencia que
              sobrevivieron a la anonimización.
              <ul className="mono">
                {report.leaks.slice(0, 15).map((lk, i) => (
                  <li key={i}>
                    [{lk.label}] {lk.text}{" "}
                    <span className="muted">(ejemplo {lk.example + 1})</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="notice">
              Sin fugas: ningún dato personal de referencia sobrevivió a la
              anonimización.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}
