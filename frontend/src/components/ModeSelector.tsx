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
  return (
    <fieldset className="mode-selector" disabled={disabled}>
      <legend>Modo de detección</legend>
      {MODES.map((m) => (
        <label key={m} className={`chip ${value === m ? "selected" : ""}`}>
          <input
            type="radio"
            name="mode"
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
