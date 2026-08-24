import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// `globals: false` in vitest.config.ts means Testing Library's automatic
// cleanup (which only registers itself when it detects a global `afterEach`)
// never kicks in, so each render leaks into the next test's DOM. Do it
// explicitly instead.
afterEach(cleanup);
