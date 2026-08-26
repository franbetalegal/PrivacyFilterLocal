import { useEffect, useState } from "react";
import { getHealth, installModelUpdate, type Health } from "../api";

const CATEGORIES: [string, string][] = [
  ["PERSON", "Nombres de persona"],
  ["EMAIL", "Direcciones de correo electrónico"],
  ["PHONE", "Números de teléfono"],
  ["ADDRESS", "Direcciones postales"],
  ["DATE", "Fechas personales"],
  ["URL", "Enlaces web"],
  ["ACCOUNT_NUMBER", "Cuentas bancarias y tarjetas"],
  ["SECRET", "Contraseñas y claves de API"],
];

export default function InfoTab() {
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  async function onUpdateModel() {
    setBusy(true);
    setMsg("Buscando actualizaciones del modelo…");
    try {
      const res = await installModelUpdate();
      setMsg(res.message);
    } catch (e) {
      setMsg(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="tab-content">
      <h2>Categorías de datos personales</h2>
      <table>
        <thead>
          <tr>
            <th>Categoría</th>
            <th>Descripción</th>
          </tr>
        </thead>
        <tbody>
          {CATEGORIES.map(([cat, desc]) => (
            <tr key={cat}>
              <td>
                <code>{cat}</code>
              </td>
              <td>{desc}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Formatos admitidos</h2>
      <ul>
        <li>Texto: .txt, .md, .csv, .json, .log, .py, .js, .xml, .html</li>
        <li>PDF: .pdf (devuelve un PDF anonimizado)</li>
        <li>DOCX: .docx (devuelve un DOCX anonimizado)</li>
      </ul>

      <h2>Seguridad</h2>
      <ul>
        <li>100% local: no se envía nada a internet</li>
        <li>El modelo se ejecuta en su equipo</li>
        <li>Licencia Apache 2.0</li>
      </ul>

      <h2>Modelo</h2>
      {health?.device_label && (
        <p>
          Ejecutándose en:{" "}
          <code>{health.device_label}</code>
          {health.device === "cpu" && (
            <span className="muted">
              {" "}— añada una GPU CUDA o utilice un Mac con Apple Silicon para
              activar la aceleración por hardware.
            </span>
          )}
        </p>
      )}
      <button className="btn" onClick={onUpdateModel} disabled={busy}>
        {busy ? "Trabajando…" : "Actualizar modelo"}
      </button>
      {msg && <p className="muted">{msg}</p>}
    </div>
  );
}
