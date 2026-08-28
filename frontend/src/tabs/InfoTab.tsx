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
        <li>
          Los documentos no salen de su equipo: se analizan y se anonimizan en
          local, y nunca se envían a ningún servidor
        </li>
        <li>El modelo se ejecuta en su equipo</li>
        <li>
          Las únicas conexiones que hace la aplicación son para descargar los
          modelos la primera vez y para comprobar si hay versiones nuevas de la
          aplicación y de los modelos
        </li>
        <li>Licencia Apache 2.0</li>
      </ul>

      <h2>Componentes de detección</h2>
      <p className="muted">
        Las tres capas trabajan juntas. Si falta la de nombres, los que están
        escritos en mayúsculas no se detectan y el documento resultante parece
        anonimizado sin estarlo.
      </p>
      <table>
        <thead>
          <tr>
            <th>Componente</th>
            <th>Estado</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Modelo PII</td>
            <td>
              {health?.components?.opf.available
                ? "Instalado"
                : "No disponible"}
            </td>
          </tr>
          {health?.components?.ner.models.map((model) => (
            <tr key={model.name}>
              <td>
                Nombres y lugares (
                {model.name.startsWith("ca_") ? "catalán" : "castellano"})
              </td>
              <td>
                {model.present
                  ? `Instalado${model.version ? ` · v${model.version}` : ""}`
                  : "No disponible"}
              </td>
            </tr>
          ))}
          <tr>
            <td>OCR de documentos escaneados</td>
            <td>
              {health?.components?.ocr.available
                ? "Instalado"
                : "No disponible — los PDF escaneados no se podrán leer"}
            </td>
          </tr>
        </tbody>
      </table>

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
