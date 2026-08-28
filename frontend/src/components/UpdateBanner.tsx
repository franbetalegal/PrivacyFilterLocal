import { useEffect, useRef, useState } from "react";
import {
  getHealth,
  getUpdates,
  installAppUpdate,
  installModelUpdate,
  type UpdatesInfo,
} from "../api";

/** How often to ask the restarting backend whether it is back. */
const RESTART_POLL_MS = 1500;
/** Give up waiting for the restart after this long. */
const RESTART_TIMEOUT_MS = 60_000;

export default function UpdateBanner() {
  const [info, setInfo] = useState<UpdatesInfo | null>(null);
  const [appDismissed, setAppDismissed] = useState(false);
  const [modelDismissed, setModelDismissed] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Cancels an in-flight restart poll when the component goes away.
  const pollingRef = useRef(true);

  useEffect(() => {
    getUpdates()
      .then(setInfo)
      .catch(() => setInfo(null));
  }, []);

  useEffect(() => {
    pollingRef.current = true;
    return () => {
      pollingRef.current = false;
    };
  }, []);

  /** Reload once a *different* server process answers.
   *
   *  Comparing instance ids matters: for the first second or two after the
   *  update response the old process is still listening, so a plain "is health
   *  reachable?" check would reload against the version we just replaced.
   */
  async function waitForRestart(previousInstance: string | undefined) {
    const deadline = Date.now() + RESTART_TIMEOUT_MS;
    while (pollingRef.current && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, RESTART_POLL_MS));
      if (!pollingRef.current) return;
      try {
        const h = await getHealth();
        if (h.instance && h.instance !== previousInstance) {
          window.location.reload();
          return;
        }
      } catch {
        // Server is down mid-restart; keep waiting.
      }
    }
    if (pollingRef.current) {
      setMessage(
        "La actualización se instaló, pero el servidor no ha vuelto a responder. " +
          "Cierre esta ventana y vuelva a abrir el programa.",
      );
    }
  }

  async function onAppUpdate() {
    setBusy(true);
    setMessage("Actualizando la aplicación…");
    let previousInstance: string | undefined;
    try {
      previousInstance = (await getHealth()).instance;
    } catch {
      /* Without an id we still restart; the reload just waits for any answer. */
    }
    try {
      const res = await installAppUpdate();
      if (res.restarting) {
        // Stay busy: the buttons must not invite a second update while the
        // server is coming back up.
        setMessage("Actualización instalada. Reiniciando el servidor…");
        await waitForRestart(previousInstance);
        setBusy(false);
        return;
      }
      setMessage(res.message);
    } catch (e) {
      setMessage(`Error: ${e instanceof Error ? e.message : String(e)}`);
    }
    setBusy(false);
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
