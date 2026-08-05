import { useEffect, useState } from "react";
import { getVersion, getHealth, shutdown, DIAGNOSTICS_URL, type Health } from "./api";
import TextTab from "./tabs/TextTab";
import FilesTab from "./tabs/FilesTab";
import DictionaryTab from "./tabs/DictionaryTab";
import EvaluationTab from "./tabs/EvaluationTab";
import InfoTab from "./tabs/InfoTab";
import UpdateBanner from "./components/UpdateBanner";
import PreparingScreen from "./components/PreparingScreen";

type TabKey = "text" | "files" | "dictionary" | "evaluation" | "info";

const TABS: { key: TabKey; label: string }[] = [
  { key: "text", label: "Text" },
  { key: "files", label: "Files" },
  { key: "dictionary", label: "Diccionario" },
  { key: "evaluation", label: "Evaluación" },
  { key: "info", label: "Info" },
];

export default function App() {
  const [tab, setTab] = useState<TabKey>("text");
  const [version, setVersion] = useState<string>("");
  const [health, setHealth] = useState<Health | null>(null);
  const [quitting, setQuitting] = useState(false);

  useEffect(() => {
    getVersion()
      .then(setVersion)
      .catch(() => setVersion("unknown"));
  }, []);

  // Poll health until the backend is ready (model not downloading / no error).
  useEffect(() => {
    let active = true;
    let timer: number | undefined;

    async function poll() {
      try {
        const h = await getHealth();
        if (!active) return;
        setHealth(h);
        if (h.downloading || h.error) {
          timer = window.setTimeout(poll, 1500);
        }
      } catch {
        if (!active) return;
        setHealth(null); // server not reachable yet
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
          <h2>Server stopped</h2>
          <p className="muted">You can close this window.</p>
        </div>
      </div>
    );
  }

  const ready = health !== null && !health.downloading && !health.error;
  if (!ready) {
    return (
      <div className="app">
        <PreparingScreen health={health} />
      </div>
    );
  }

  return (
    <div className="app">
      <UpdateBanner />

      <header className="header">
        <div className="header-row">
          <div>
            <h1>Privacy Filter — Local</h1>
            <p className="subtitle">
              100% local PII detection{version ? ` · v${version}` : ""}
            </p>
          </div>
          <div className="header-actions">
            <a className="btn" href={DIAGNOSTICS_URL} download title="Download a support bundle">
              Diagnostics
            </a>
            <button className="btn" onClick={onQuit} title="Stop the local server">
              Quit
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
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="panel">
        {/* Each tab is mounted only when active; switching is plain React
            state, so there is no Gradio/Svelte reactive loop to freeze. */}
        {tab === "text" && <TextTab />}
        {tab === "files" && <FilesTab />}
        {tab === "dictionary" && <DictionaryTab />}
        {tab === "evaluation" && <EvaluationTab />}
        {tab === "info" && <InfoTab />}
      </main>
    </div>
  );
}
