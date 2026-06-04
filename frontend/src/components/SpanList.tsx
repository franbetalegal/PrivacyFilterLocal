import { type DetectedSpan } from "../api";

/** Collapsible table of detected PII entities (replaces the old <details>). */
export default function SpanList({ spans }: { spans: DetectedSpan[] }) {
  if (spans.length === 0) return null;
  return (
    <details className="spans">
      <summary>Show {spans.length} detected entities</summary>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Type</th>
            <th>Original</th>
            <th>Replacement</th>
          </tr>
        </thead>
        <tbody>
          {spans.map((s, i) => (
            <tr key={i}>
              <td>{i + 1}</td>
              <td>
                <code>{s.label}</code>
              </td>
              <td>{s.text}</td>
              <td>{s.placeholder}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
