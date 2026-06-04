import { useEffect, useState } from "react";
import { getVersion } from "./api";
import TextTab from "./tabs/TextTab";
import FilesTab from "./tabs/FilesTab";
import InfoTab from "./tabs/InfoTab";
import UpdateBanner from "./components/UpdateBanner";

type TabKey = "text" | "files" | "info";

const TABS: { key: TabKey; label: string }[] = [
  { key: "text", label: "Text" },
  { key: "files", label: "Files" },
  { key: "info", label: "Info" },
];

export default function App() {
  const [tab, setTab] = useState<TabKey>("text");
  const [version, setVersion] = useState<string>("");

  useEffect(() => {
    getVersion()
      .then(setVersion)
      .catch(() => setVersion("unknown"));
  }, []);

  return (
    <div className="app">
      <UpdateBanner />

      <header className="header">
        <h1>Privacy Filter — Local</h1>
        <p className="subtitle">
          100% local PII detection{version ? ` · v${version}` : ""}
        </p>
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
        {tab === "info" && <InfoTab />}
      </main>
    </div>
  );
}
