import { render, screen, within } from "@testing-library/react";
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

  it("selects the mode when the label text is clicked, not just the circle", async () => {
    const onChange = vi.fn();
    render(<ModeSelector value="balanced" onChange={onChange} />);
    // Clicking the label (what the user aims at) has to reach the input. The
    // htmlFor/id pair is what guarantees it independently of the nesting.
    await userEvent.click(screen.getByText(MODE_LABEL.conservative));
    expect(onChange).toHaveBeenCalledWith("conservative");
  });

  it("keeps two mounted selectors in independent radio groups", async () => {
    // A visited tab stays mounted, so Text and Files can render a selector at
    // the same time. With a shared `name` the browser treats all six radios as
    // one group, so checking one selector's mode unchecks the other's.
    const onChange = vi.fn();
    render(
      <>
        <div data-testid="first">
          <ModeSelector value="balanced" onChange={() => {}} />
        </div>
        <div data-testid="second">
          <ModeSelector value="aggressive" onChange={onChange} />
        </div>
      </>,
    );

    const first = within(screen.getByTestId("first"));
    const second = within(screen.getByTestId("second"));
    expect(first.getByRole("radio", { name: MODE_LABEL.balanced })).toBeChecked();
    expect(second.getByRole("radio", { name: MODE_LABEL.aggressive })).toBeChecked();

    await userEvent.click(second.getByText(MODE_LABEL.conservative));
    expect(onChange).toHaveBeenCalledWith("conservative");
    // The first selector is untouched by activity in the second one.
    expect(first.getByRole("radio", { name: MODE_LABEL.balanced })).toBeChecked();
  });

  it("disables the fieldset when disabled", () => {
    // jsdom doesn't implement fieldset-disables-descendants (unlike real
    // browsers), so we assert on the attribute this component actually
    // sets rather than the descendant `.disabled` state jsdom can't derive.
    render(<ModeSelector value="balanced" onChange={() => {}} disabled />);
    expect(screen.getByRole("group")).toBeDisabled();
  });
});
