import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MODE_LABEL } from "../api";
import ModeSelector from "./ModeSelector";

describe("ModeSelector", () => {
  it("marks the current mode as checked", () => {
    render(<ModeSelector value="balanced" onChange={() => {}} />);
    expect(
      screen.getByRole("radio", { name: MODE_LABEL.balanced }),
    ).toBeChecked();
    expect(
      screen.getByRole("radio", { name: MODE_LABEL.conservative }),
    ).not.toBeChecked();
  });

  it("calls onChange with the selected mode", async () => {
    const onChange = vi.fn();
    render(<ModeSelector value="balanced" onChange={onChange} />);
    await userEvent.click(
      screen.getByRole("radio", { name: MODE_LABEL.aggressive }),
    );
    expect(onChange).toHaveBeenCalledWith("aggressive");
  });

  it("disables the fieldset when disabled", () => {
    // jsdom doesn't implement fieldset-disables-descendants (unlike real
    // browsers), so we assert on the attribute this component actually
    // sets rather than the descendant `.disabled` state jsdom can't derive.
    render(<ModeSelector value="balanced" onChange={() => {}} disabled />);
    expect(screen.getByRole("group")).toBeDisabled();
  });
});
