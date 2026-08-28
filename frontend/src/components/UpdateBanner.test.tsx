import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import UpdateBanner from "./UpdateBanner";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    getUpdates: vi.fn(),
    getHealth: vi.fn(),
    installAppUpdate: vi.fn(),
    installModelUpdate: vi.fn(),
  };
});

const OLD_INSTANCE = "instance-before";
const NEW_INSTANCE = "instance-after";

function health(instance: string) {
  return {
    model_loaded: true,
    loading: false,
    downloading: false,
    download_pct: 100,
    error: null,
    instance,
  };
}

const reload = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getUpdates).mockResolvedValue({
    app: {
      update_available: true,
      current_version: "2.6.0",
      latest_version: "2.6.1",
    },
    model: { update_available: false },
  } as unknown as api.UpdatesInfo);
  // jsdom's location.reload is not implementable; swap the whole accessor.
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, reload },
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("UpdateBanner", () => {
  it("waits for a new server instance before reloading the page", async () => {
    vi.mocked(api.installAppUpdate).mockResolvedValue({
      status: "ok",
      message: "Updated from 2.6.0 to 2.6.1",
      restarting: true,
    });
    // First reply still comes from the outgoing process: reloading there would
    // load the very version we just replaced.
    vi.mocked(api.getHealth)
      .mockResolvedValueOnce(health(OLD_INSTANCE))
      .mockRejectedValueOnce(new Error("server down"))
      .mockResolvedValue(health(NEW_INSTANCE));

    render(<UpdateBanner />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Actualizar ahora" }),
    );

    // Spanish prose comes from here, not from the backend's message.
    expect(
      await screen.findByText("Actualización instalada. Reiniciando el servidor…"),
    ).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();

    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1), { timeout: 10000 });
  }, 15000);

  it("shows the backend message when no restart is coming", async () => {
    vi.mocked(api.getHealth).mockResolvedValue(health(OLD_INSTANCE));
    vi.mocked(api.installAppUpdate).mockResolvedValue({
      status: "noop",
      message: "No app update available.",
    });

    render(<UpdateBanner />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Actualizar ahora" }),
    );

    expect(
      await screen.findByText("No app update available."),
    ).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
  });
});
