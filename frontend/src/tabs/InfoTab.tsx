import { useState } from "react";
import { installModelUpdate } from "../api";

const CATEGORIES: [string, string][] = [
  ["PERSON", "Person names"],
  ["EMAIL", "Email addresses"],
  ["PHONE", "Phone numbers"],
  ["ADDRESS", "Postal addresses"],
  ["DATE", "Personal dates"],
  ["URL", "Web links"],
  ["ACCOUNT_NUMBER", "Bank accounts, cards"],
  ["SECRET", "Passwords, API keys"],
];

export default function InfoTab() {
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onUpdateModel() {
    setBusy(true);
    setMsg("Checking / downloading model update…");
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
      <h2>PII categories</h2>
      <table>
        <thead>
          <tr>
            <th>Category</th>
            <th>Description</th>
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

      <h2>Supported formats</h2>
      <ul>
        <li>Text: .txt, .md, .csv, .json, .log, .py, .js, .xml, .html</li>
        <li>PDF: .pdf (returns a redacted PDF)</li>
        <li>DOCX: .docx (returns a redacted DOCX)</li>
      </ul>

      <h2>Security</h2>
      <ul>
        <li>100% local — nothing is sent to the internet</li>
        <li>The model runs on your PC</li>
        <li>Apache 2.0 license</li>
      </ul>

      <h2>Model</h2>
      <button className="btn" onClick={onUpdateModel} disabled={busy}>
        {busy ? "Working…" : "Update model"}
      </button>
      {msg && <p className="muted">{msg}</p>}
    </div>
  );
}
