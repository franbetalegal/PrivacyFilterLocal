import { useEffect, useRef, useState } from "react";
import {
  getDictionary,
  addDictEntry,
  updateDictEntry,
  deleteDictEntry,
  importDictionary,
  DICTIONARY_EXPORT_URL,
  type DictEntry,
  type MatchMode,
} from "../api";

const MATCH_LABEL: Record<MatchMode, string> = {
  smart: "Inteligente (may/acentos, palabra completa)",
  exact: "Exacto (respeta may/min)",
  regex: "Expresión regular (avanzado)",
};

export default function DictionaryTab() {
  const [terms, setTerms] = useState<DictEntry[]>([]);
  const [labels, setLabels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // New-entry form.
  const [term, setTerm] = useState("");
  const [label, setLabel] = useState("EMPRESA");
  const [match, setMatch] = useState<MatchMode>("smart");
  const [saving, setSaving] = useState(false);

  const fileRef = useRef<HTMLInputElement | null>(null);

  async function refresh() {
    setLoading(true);
    try {
      const info = await getDictionary();
      setTerms(info.terms);
      setLabels(info.labels);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onAdd() {
    const value = term.trim();
    if (!value) {
      setError("Escribe un término.");
      return;
    }
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      await addDictEntry({ term: value, label, match, enabled: true });
      setTerm("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function onToggle(entry: DictEntry) {
    try {
      await updateDictEntry(entry.id, { enabled: !entry.enabled });
      setTerms((ts) =>
        ts.map((t) => (t.id === entry.id ? { ...t, enabled: !t.enabled } : t)),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function onDelete(entry: DictEntry) {
    try {
      await deleteDictEntry(entry.id);
      setTerms((ts) => ts.filter((t) => t.id !== entry.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function onImportFile(file: File) {
    setError(null);
    setNotice(null);
    try {
      const parsed = JSON.parse(await file.text());
      const list = Array.isArray(parsed) ? parsed : parsed.terms;
      if (!Array.isArray(list)) {
        throw new Error("El archivo no contiene una lista de términos.");
      }
      const res = await importDictionary(list);
      setNotice(
        `Importados ${res.added} término(s) · ${res.skipped} ya existían · ` +
          `${res.invalid} descartados.`,
      );
      await refresh();
    } catch (e) {
      setError(
        e instanceof Error ? `No se pudo importar: ${e.message}` : String(e),
      );
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="tab-content">
      <p className="muted">
        Términos que se anonimizarán <strong>siempre</strong>, además de lo que
        detecta el modelo. Ideal para las partes del asunto y nombres de empresa
        que conviene ocultar sin depender de la detección automática. El
        diccionario se guarda en tu equipo y puedes exportarlo para compartirlo.
      </p>

      <div className="dict-form">
        <div className="col grow">
          <label htmlFor="dict-term">Término</label>
          <input
            id="dict-term"
            value={term}
            placeholder="STEEL PROPERTY SL"
            disabled={saving}
            onChange={(e) => setTerm(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onAdd();
            }}
          />
        </div>
        <div className="col">
          <label htmlFor="dict-label">Etiqueta</label>
          <input
            id="dict-label"
            list="dict-labels"
            value={label}
            disabled={saving}
            onChange={(e) => setLabel(e.target.value.toUpperCase())}
          />
          <datalist id="dict-labels">
            {labels.map((l) => (
              <option key={l} value={l} />
            ))}
          </datalist>
        </div>
        <div className="col">
          <label htmlFor="dict-match">Coincidencia</label>
          <select
            id="dict-match"
            value={match}
            disabled={saving}
            onChange={(e) => setMatch(e.target.value as MatchMode)}
          >
            {(Object.keys(MATCH_LABEL) as MatchMode[]).map((m) => (
              <option key={m} value={m}>
                {MATCH_LABEL[m]}
              </option>
            ))}
          </select>
        </div>
        <div className="col">
          <label>&nbsp;</label>
          <button className="btn primary" onClick={onAdd} disabled={saving}>
            {saving ? "Añadiendo…" : "Añadir"}
          </button>
        </div>
      </div>

      <div className="row dict-toolbar">
        <a className="btn" href={DICTIONARY_EXPORT_URL} download>
          Exportar (JSON)
        </a>
        <button className="btn" onClick={() => fileRef.current?.click()}>
          Importar (JSON)
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onImportFile(f);
          }}
        />
        <span className="muted">{terms.length} término(s)</span>
      </div>

      {error && <p className="error">Error: {error}</p>}
      {notice && <p className="notice">{notice}</p>}

      {loading ? (
        <p className="muted">Cargando…</p>
      ) : terms.length === 0 ? (
        <p className="muted">
          Aún no hay términos. Añade el primero arriba.
        </p>
      ) : (
        <table className="dict-table">
          <thead>
            <tr>
              <th>Activo</th>
              <th>Término</th>
              <th>Etiqueta</th>
              <th>Coincidencia</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {terms.map((t) => (
              <tr key={t.id} className={t.enabled ? "" : "disabled-row"}>
                <td>
                  <input
                    type="checkbox"
                    checked={t.enabled}
                    onChange={() => onToggle(t)}
                    aria-label={`Activar ${t.term}`}
                  />
                </td>
                <td className="mono">{t.term}</td>
                <td>
                  <span className="tag">[{t.label}]</span>
                </td>
                <td className="muted">{t.match}</td>
                <td>
                  <button
                    className="btn small danger"
                    onClick={() => onDelete(t)}
                    title="Eliminar término"
                  >
                    Eliminar
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
