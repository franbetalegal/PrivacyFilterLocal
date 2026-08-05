import { type DetectedSpan } from "../api";

interface SpanListProps {
  spans: DetectedSpan[];
  /** When provided, renders a checkbox per row so the user can un-check
   *  spans they don't want redacted. Passing null (or omitting) disables
   *  the review UI and the list becomes purely informational. */
  selection?: readonly boolean[];
  onToggle?: (index: number) => void;
  onToggleAll?: (checked: boolean) => void;
}

/** Collapsible table of detected PII entities. When ``selection`` and
 *  ``onToggle`` are provided, becomes an interactive review list — every
 *  span has a checkbox, and the caller decides what to do with the
 *  survivors (typically re-submit them to /api/redact-file/apply). */
export default function SpanList({
  spans,
  selection,
  onToggle,
  onToggleAll,
}: SpanListProps) {
  if (spans.length === 0) return null;
  const interactive = selection !== undefined && onToggle !== undefined;
  const keptCount = interactive ? selection!.filter(Boolean).length : spans.length;

  return (
    <details className="spans" open={interactive}>
      <summary>
        {interactive
          ? `${keptCount} de ${spans.length} entidades marcadas para anonimizar`
          : `Show ${spans.length} detected entities`}
      </summary>
      <table>
        <thead>
          <tr>
            {interactive && (
              <th>
                <input
                  type="checkbox"
                  checked={keptCount === spans.length}
                  ref={(el) => {
                    if (el) el.indeterminate = keptCount > 0 && keptCount < spans.length;
                  }}
                  onChange={(e) => onToggleAll?.(e.target.checked)}
                  aria-label="Seleccionar todas"
                />
              </th>
            )}
            <th>#</th>
            <th>Type</th>
            <th>Original</th>
            <th>Replacement</th>
          </tr>
        </thead>
        <tbody>
          {spans.map((s, i) => {
            const checked = interactive ? selection![i] : true;
            return (
              <tr key={i} style={{ opacity: interactive && !checked ? 0.4 : 1 }}>
                {interactive && (
                  <td>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => onToggle!(i)}
                      aria-label={`Incluir "${s.text}"`}
                    />
                  </td>
                )}
                <td>{i + 1}</td>
                <td>
                  <code>{s.label}</code>
                </td>
                <td>{s.text}</td>
                <td>{s.placeholder}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </details>
  );
}
