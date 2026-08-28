import { useId } from "react";
import { MODES, MODE_LABEL, type Mode } from "../api";

/** Radio group for the precision ↔ recall operating point. Kept as a shared
 *  component so both the Text and Files tabs render an identical control. */
export default function ModeSelector({
  value,
  onChange,
  disabled = false,
}: {
  value: Mode;
  onChange: (m: Mode) => void;
  disabled?: boolean;
}) {
  // A visited tab stays mounted, so two selectors can share the document. With
  // a hardcoded name their radios would form a single group: the browser keeps
  // one checked across both, fighting React's controlled `checked` and leaving
  // clicks looking like they did nothing. One group per instance fixes it, and
  // the id/htmlFor pair makes the label association explicit on top of nesting.
  const uid = useId();
  return (
    <fieldset className="mode-selector" disabled={disabled}>
      <legend>Modo de detección</legend>
      {MODES.map((m) => (
        <label
          key={m}
          className={`chip ${value === m ? "selected" : ""}`}
          htmlFor={`${uid}-${m}`}
        >
          <input
            id={`${uid}-${m}`}
            type="radio"
            name={`mode-${uid}`}
            value={m}
            checked={value === m}
            onChange={() => onChange(m)}
          />
          {MODE_LABEL[m]}
        </label>
      ))}
    </fieldset>
  );
}
