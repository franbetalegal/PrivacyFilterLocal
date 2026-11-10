import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MODES,
  MODE_LABEL,
  SERVER_UNREACHABLE_MESSAGE,
  ServerUnreachableError,
  downloadUrl,
  getVersion,
  isAbort,
} from "./api";

describe("isAbort", () => {
  it("recognizes a fetch AbortError", () => {
    const err = new DOMException("aborted", "AbortError");
    expect(isAbort(err)).toBe(true);
  });

  it("rejects other errors", () => {
    expect(isAbort(new Error("boom"))).toBe(false);
    expect(isAbort(new DOMException("x", "NotAllowedError"))).toBe(false);
  });
});

describe("downloadUrl", () => {
  it("builds the download path from a token", () => {
    expect(downloadUrl("abc123")).toBe("/api/download/abc123");
  });
});

describe("MODE_LABEL", () => {
  it("has a label for every mode", () => {
    for (const mode of MODES) {
      expect(MODE_LABEL[mode]).toBeTruthy();
    }
  });
});

describe("getVersion", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the version from a successful response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ version: "2.5.0" }),
      }),
    );
    await expect(getVersion()).resolves.toBe("2.5.0");
  });

  it("throws the server-provided detail on a failed response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        statusText: "Internal Server Error",
        json: async () => ({ detail: "model not loaded" }),
      }),
    );
    await expect(getVersion()).rejects.toThrow("model not loaded");
  });
});

describe("network failures", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("replaces the browser's 'Failed to fetch' with an explanation", async () => {
    // What Chrome and Edge throw when the server process is gone.
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(getVersion()).rejects.toBeInstanceOf(ServerUnreachableError);
    await expect(getVersion()).rejects.toThrow(SERVER_UNREACHABLE_MESSAGE);
  });

  it("keeps a user cancellation recognisable as an abort", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")),
    );
    const err = await getVersion().catch((e: unknown) => e);
    expect(isAbort(err)).toBe(true);
  });
});
