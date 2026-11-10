import { useEffect, useState } from "react";
import { getVersion, getHealth, isReady, shutdown, DIAGNOSTICS_URL, type Health } from "./api";
import TextTab from "./tabs/TextTab";
import FilesTab from "./tabs/FilesTab";
import DictionaryTab from "./tabs/DictionaryTab";
import MarkdownTab from "./tabs/MarkdownTab";
import EvaluationTab from "./tabs/EvaluationTab";
import InfoTab from "./tabs/InfoTab";
import UpdateBanner from "./components/UpdateBanner";
import PreparingScreen from "./components/PreparingScreen";
import ComponentWarnings from "./components/ComponentWarnings";

type TabKey =
  | "text"
  | "files"
  | "markdown"
  | "dictionary"
  | "evaluation"
  | "info";

const TABS: { key: TabKey; label: string }[] = [
  { key: "text", label: "Texto" },
  { key: "files", label: "Archivos" },
  { key: "markdown", label: "Markdown" },
  { key: "dictionary", label: "Diccionario" },
  { key: "evaluation", label: "Evaluación" },
  { key: "info", label: "Información" },
];

export default function App() {
  const [tab, setTab] = useState<TabKey>("text");
  // Tabs are mounted the first time they are opened and then kept mounted.
  // They used to be unmounted on every switch, which destroyed whatever the tab
  // was holding: leaving the Archivos tab mid-analysis threw away the file, the
  // detected spans and the review selection, while the upload carried on
  // server-side with nowhere to land. It also made the evaluation checkbox
  // unusable, since the example is saved in the second step and the file needed
  // for that step was already gone.
  const [opened, setOpened] = useState<Set<TabKey>>(() => new Set<TabKey>(["text"]));

  function openTab(key: TabKey) {
    setOpened((prev) => (prev.has(key) ? prev : new Set(prev).add(key)));
    setTab(key);
  }
  const [version, setVersion] = useState<string>("");
  const [health, setHealth] = useState<Health | null>(null);
  const [quitting, setQuitting] = useState(false);
  // Once the app has been usable, a server problem must not swap the whole UI
  // for the "Preparing" screen: that would unmount the tabs and throw away the
  // user's files and review. A banner says what happened instead.
  const [everReady, setEverReady] = useState(false);
  const [serverLost, setServerLost] = useState(false);

  useEffect(() => {
    getVersion()
      .then(setVersion)
      .catch(() => setVersion("desconocida"));
  }, []);

  // Poll health for the whole session. Fast while preparing; slower once ready,
  // which is how a server that died mid-session gets noticed. It used to stop
  // at "ready", and the only symptom of a dead server was the browser's raw
  // "Failed to fetch" on the next action.
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    let failures = 0;
    let wasReady = false;

    async function poll() {
      try {
        const h = await getHealth();
        if (!active) return;
        failures = 0;
        setHealth(h);
        setServerLost(false);
        if (isReady(h)) {
          wasReady = true;
          setEverReady(true);
        }
        timer = window.setTimeout(poll, isReady(h) ? 5000 : 1500);
      } catch {
        if (!active) return;
        failures += 1;
        // One miss can be a busy moment; two in a row is a server that is gone.
        if (wasReady) {
          if (failures >= 2) setServerLost(true);
        } else {
          setHealth(null); // server not reachable yet (still starting)
        }
        timer = window.setTimeout(poll, 1500);
      }
    }
    poll();
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  async function onQuit() {
    setQuitting(true);
    try {
      await shutdown();
    } catch {
      /* the server exits, so the request may not return cleanly */
    }
  }

  if (quitting) {
    return (
      <div className="app">
        <div className="prepare">
          <h2>Servidor detenido</h2>
          <p className="muted">Ya puede cerrar esta ventana.</p>
        </div>
      </div>
    );
  }

  if (!everReady && !isReady(health)) {
    return (
      <div className="app">
        <PreparingScreen health={health} />
      </div>
    );
  }

  return (
    <div className="app">
      {serverLost ? (
        <div className="server-banner error-banner" role="alert">
          <strong>Se ha perdido la conexión con el servidor local.</strong>{" "}
          Puede que se haya cerrado o que el equipo se haya quedado sin memoria.
          Si no se recupera solo en unos segundos, vuelva a abrir la aplicación.
          Los documentos que estaban en proceso tendrán que enviarse de nuevo.
        </div>
      ) : !isReady(health) ? (
        <div className="server-banner" role="status">
          <span className="spinner" aria-hidden="true" /> El servidor se ha
          reiniciado y está cargando de nuevo los modelos. Espere a que
          desaparezca este aviso antes de procesar otro documento.
        </div>
      ) : null}
      <UpdateBanner />
      <ComponentWarnings health={health} />

      <header className="header">
        <div className="header-row">
          <div>
            <h1>Privacy Filter — Local</h1>
            <p className="subtitle">
              Detección de datos personales 100% local
              {version ? ` · v${version}` : ""}
            </p>
          </div>
          <div className="header-actions">
            <a
              className="btn"
              href={DIAGNOSTICS_URL}
              download
              title="Descargar un paquete de diagnóstico para soporte"
            >
              Diagnóstico
            </a>
            <button className="btn" onClick={onQuit} title="Detener el servidor local">
              Salir
            </button>
          </div>
        </div>
      </header>

      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            className={`tab ${tab === t.key ? "active" : ""}`}
            onClick={() => openTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="panel">
        {/* Mounted on first open, hidden rather than unmounted afterwards, so
            work in progress survives a tab switch. Lazily, so opening the app
            does not fire every tab's start-up requests at once. */}
        {TABS.map((t) =>
          opened.has(t.key) ? (
            <div key={t.key} role="tabpanel" hidden={tab !== t.key}>
              {t.key === "text" && <TextTab />}
              {t.key === "files" && <FilesTab />}
              {t.key === "markdown" && <MarkdownTab />}
              {t.key === "dictionary" && <DictionaryTab />}
              {t.key === "evaluation" && <EvaluationTab />}
              {t.key === "info" && <InfoTab />}
            </div>
          ) : null,
        )}
      </main>
    </div>
  );
}
