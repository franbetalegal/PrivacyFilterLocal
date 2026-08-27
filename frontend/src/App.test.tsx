import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Partial mock: the real module keeps its constants (DIAGNOSTICS_URL,
// MODE_LABEL and friends, which the tabs read), and only the calls made on
// mount are stubbed. Enumerating every export by hand breaks whenever one is
// added.
vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return {
    ...actual,
    getVersion: vi.fn().mockResolvedValue('2.6.0'),
    getHealth: vi.fn().mockResolvedValue({
      model_loaded: true,
      loading: false,
      downloading: false,
      download_pct: 100,
      error: null,
    }),
    getUpdates: vi.fn().mockResolvedValue({ app: {}, model: {} }),
    quit: vi.fn().mockResolvedValue(undefined),
  };
});

// Any call a tab makes on mount that is not stubbed above lands here rather
// than on a real socket.
globalThis.fetch = vi.fn().mockResolvedValue({
  ok: true,
  status: 200,
  statusText: 'OK',
  json: async () => ({}),
}) as unknown as typeof fetch;

import App from './App';

/**
 * Tabs must survive a switch.
 *
 * They used to be rendered as `{tab === "files" && <FilesTab />}`, which
 * unmounts the component the moment you leave. Reported from real use: leaving
 * the Archivos tab while a file was being analysed threw the file away, and the
 * "save as evaluation example" checkbox could never take effect, because the
 * example is saved in a second step that needs the file the unmount had already
 * discarded.
 */
describe('tab lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps a visited tab mounted after switching away', async () => {
    const user = userEvent.setup();
    render(<App />);
    // The app shows a preparing screen until the health poll resolves.
    await screen.findByRole('tab', { name: 'Archivos' });

    await user.click(screen.getByRole('tab', { name: 'Archivos' }));
    await user.click(screen.getByRole('tab', { name: 'Información' }));

    // Still in the document, merely hidden — this is the whole fix.
    const panels = document.querySelectorAll('[role="tabpanel"]');
    expect(panels.length).toBeGreaterThan(1);
    const hidden = Array.from(panels).filter((p) => p.hasAttribute('hidden'));
    expect(hidden.length).toBeGreaterThan(0);
  });

  it('shows exactly one panel at a time', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('tab', { name: 'Archivos' });

    await user.click(screen.getByRole('tab', { name: 'Archivos' }));
    await user.click(screen.getByRole('tab', { name: 'Información' }));

    const panels = Array.from(document.querySelectorAll('[role="tabpanel"]'));
    const visible = panels.filter((p) => !p.hasAttribute('hidden'));
    expect(visible).toHaveLength(1);
  });

  it('does not mount a tab that has never been opened', async () => {
    render(<App />);
    await screen.findByRole('tab', { name: 'Archivos' });

    // Only the initial tab exists; opening the app must not fire every tab's
    // start-up requests at once.
    const panels = document.querySelectorAll('[role="tabpanel"]');
    expect(panels).toHaveLength(1);
  });
});
