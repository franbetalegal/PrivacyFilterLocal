/** Visual "working" indicator: spinner + indeterminate bar + optional hint. */
export default function Processing({
  label = "Processing…",
  hint,
}: {
  label?: string;
  hint?: string;
}) {
  return (
    <div className="processing" role="status" aria-live="polite">
      <div className="processing-row">
        <span className="spinner" aria-hidden="true" />
        <span>{label}</span>
      </div>
      <div className="progress-indeterminate" aria-hidden="true">
        <div className="progress-bar" />
      </div>
      {hint && <p className="processing-hint">{hint}</p>}
    </div>
  );
}
