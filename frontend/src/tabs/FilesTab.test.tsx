import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { RedactFileResult } from '../api';

// Stub the API surface FilesTab touches. We keep the actual constants (MODES,
// DIAGNOSTICS_URL, …) so nothing that imports from './api' explodes on load.
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({
      model_loaded: true,
      loading: false,
      downloading: false,
      download_pct: 100,
      error: null,
    }),
    redactFile: vi.fn(),
    applyRedaction: vi.fn(),
  };
});

import { redactFile } from '../api';
import FilesTab from './FilesTab';

function fakeResult(overrides: Partial<RedactFileResult> = {}): RedactFileResult {
  return {
    detected_spans: [],
    warnings: [],
    elapsed: 0.5,
    download_token: 'tok-' + Math.random().toString(36).slice(2, 8),
    download_name: 'anon.pdf',
    timings: { extract: 0.1, detect: 0.3, redact: 0.1, verify: 0.0, total: 0.5 },
    verified: true,
    ...overrides,
  };
}

function pdf(name: string): File {
  return new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], name, { type: 'application/pdf' });
}

describe('FilesTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the single-file review UI when one file is chosen', async () => {
    render(<FilesTab />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, pdf('demo.pdf'));

    // Single-mode button label
    expect(await screen.findByRole('button', { name: /Procesar archivo/i })).toBeInTheDocument();
    // No queue table until we have at least 2 files
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('switches to the queue UI when two or more files are chosen', async () => {
    render(<FilesTab />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, [pdf('a.pdf'), pdf('b.pdf'), pdf('c.pdf')]);

    // Queue mode: button counts files and the table lists each one
    expect(
      await screen.findByRole('button', { name: /Procesar cola \(3 archivo/i }),
    ).toBeInTheDocument();
    expect(screen.getByText('a.pdf')).toBeInTheDocument();
    expect(screen.getByText('b.pdf')).toBeInTheDocument();
    expect(screen.getByText('c.pdf')).toBeInTheDocument();
    // All three start pending — no download links yet
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('exposes a per-file collapsible list of what was anonymized', async () => {
    vi.mocked(redactFile).mockImplementation(async (file: File) => fakeResult({
      download_name: file.name.replace('.pdf', '_ANON.pdf'),
      detected_spans: [
        { start: 0, end: 5, label: 'NOMBRE', text: 'Ana', placeholder: '[NOMBRE_1]' },
        { start: 6, end: 15, label: 'DNI',    text: '12345678Z', placeholder: '[DNI_1]' },
      ],
    }));

    render(<FilesTab />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, [pdf('a.pdf'), pdf('b.pdf')]);
    await userEvent.click(screen.getByRole('button', { name: /Procesar cola/i }));

    // Two <details> collapsibles, one per row, each with the summary text
    // "Ver 2 entidad(es) detectada(s)".
    const summaries = await screen.findAllByText(/Ver 2 entidad\(es\) detectada\(s\)/);
    expect(summaries).toHaveLength(2);
    // Expand the first and check both entity types appear.
    await userEvent.click(summaries[0]);
    expect(screen.getAllByText('NOMBRE')[0]).toBeInTheDocument();
    expect(screen.getAllByText('[DNI_1]')[0]).toBeInTheDocument();
  });

  it('processes the queue sequentially and exposes a download per file', async () => {
    const order: string[] = [];
    vi.mocked(redactFile).mockImplementation(async (file: File) => {
      order.push(file.name);
      return fakeResult({ download_name: file.name.replace('.pdf', '_ANON.pdf') });
    });

    render(<FilesTab />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, [pdf('a.pdf'), pdf('b.pdf')]);

    await userEvent.click(
      screen.getByRole('button', { name: /Procesar cola/i }),
    );

    // Backend order preserved
    await waitFor(() => expect(order).toEqual(['a.pdf', 'b.pdf']));

    // Both rows expose a download link (unique token per job)
    const links = await screen.findAllByRole('link');
    // Two per row: the main file link. Markdown was disabled → only one each.
    expect(links.length).toBe(2);
    expect(links[0].getAttribute('download')).toBe('a_ANON.pdf');
    expect(links[1].getAttribute('download')).toBe('b_ANON.pdf');
  });
});
