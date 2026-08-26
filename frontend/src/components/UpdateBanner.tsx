import { useEffect, useState } from "react";
import {
  getUpdates,
  installAppUpdate,
  installModelUpdate,
  type UpdatesInfo,
} from "../api";

export default function UpdateBanner() {
  const [info, setInfo] = useState<UpdatesInfo | null>(null);
  const [appDismissed, setAppDismissed] = useState(false);
  const [modelDismissed, setModelDismissed] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getUpdates()
      .then(setInfo)
      .catch(() => setInfo(null));
  }, []);

  async function onAppUpdate() {
    setBusy(true);
    setMessage("Actualizando la aplicación…");
    try {
      const res = await installAppUpdate();
      setMessage(res.message);
    } catch (e) {
      setMessage(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function onModelUpdate() {
    setBusy(true);
    setMessage("Actualizando el modelo…");
    try {
      const res = await installModelUpdate();
      setMessage(res.message);
    } catch (e) {
      setMessage(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  if (!info) return null;

  const showApp = info.app?.update_available && !appDismissed;
  const showModel = info.model?.update_available && !modelDismissed;
  if (!showApp && !showModel && !message) return null;

  return (
    <div className="banners">
      {showApp && (
        <div className="banner">
          <div>
            <strong>
              Hay una nueva versión disponible: v{info.app.current_version} → v
              {info.app.latest_version}
            </strong>
            {info.app.changelog && (
              <pre className="changelog">
                {info.app.changelog.split("\n").slice(0, 20).join("\n")}
              </pre>
            )}
          </div>
          <div className="banner-actions">
            <button className="btn primary" onClick={onAppUpdate} disabled={busy}>
              Actualizar ahora
            </button>
            <button className="btn" onClick={() => setAppDismissed(true)} disabled={busy}>
              Más tarde
            </button>
          </div>
        </div>
      )}

      {showModel && (
        <div className="banner">
          <div>
            <strong>
              Hay una actualización del modelo de detección de datos personales
            </strong>
            <p className="muted">
              Actual: {info.model.current_date ?? "desconocida"} → Última:{" "}
              {info.model.latest_date ?? "desconocida"}
            </p>
          </div>
          <div className="banner-actions">
            <button className="btn primary" onClick={onModelUpdate} disabled={busy}>
              Actualizar modelo
            </button>
            <button
              className="btn"
              onClick={() => setModelDismissed(true)}
              disabled={busy}
            >
              Más tarde
            </button>
          </div>
        </div>
      )}

      {message && <div className="banner-message">{message}</div>}
    </div>
  );
}
