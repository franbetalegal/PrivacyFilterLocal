import { describe, expect, it } from 'vitest';

import { renderErrorDetail, renderMessage, renderMessages } from './messages';

describe('renderMessage', () => {
  it('renders a warning with its parameters', () => {
    const text = renderMessage({ code: 'leak_detected', params: { count: 3 } });
    expect(text).toContain('3 fragmentos');
    expect(text).toContain('Revíselo');
  });

  it('uses the singular when there is exactly one leak', () => {
    const text = renderMessage({ code: 'leak_detected', params: { count: 1 } });
    expect(text).toContain('1 fragmento de');
    expect(text).not.toContain('fragmentos');
  });

  it('renders a message that takes no parameters', () => {
    expect(renderMessage({ code: 'pdf_unreadable' })).toBe('No se pudo leer el PDF.');
  });

  it('falls back to the raw code when there is no translation', () => {
    // Deliberate: an untranslated code must look wrong so review catches it,
    // rather than a warning silently disappearing from the interface.
    expect(renderMessage({ code: 'some_future_code' })).toBe('some_future_code');
  });

  it('substitutes a parameter into the sentence', () => {
    expect(renderMessage({ code: 'unsupported_file_type', params: { ext: '.exe' } }))
      .toBe('Tipo de archivo no admitido: .exe.');
  });
});

describe('renderMessages', () => {
  it('returns null when there is nothing to show', () => {
    expect(renderMessages(null)).toBeNull();
    expect(renderMessages(undefined)).toBeNull();
    expect(renderMessages([])).toBeNull();
  });

  it('joins several warnings one per line', () => {
    const text = renderMessages([
      { code: 'leak_detected', params: { count: 2 } },
      { code: 'verification_unavailable_scanned' },
    ]);
    expect(text?.split('\n')).toHaveLength(2);
  });
});

describe('renderErrorDetail', () => {
  it('renders a coded detail in Spanish', () => {
    expect(renderErrorDetail({ code: 'term_not_found' }, 'fallo'))
      .toBe('Término no encontrado.');
  });

  it('passes a plain-string detail through', () => {
    // A proxy or an older build can still answer with prose.
    expect(renderErrorDetail('Something broke', 'fallo')).toBe('Something broke');
  });

  it('uses the fallback when there is no detail at all', () => {
    expect(renderErrorDetail(null, 'Error de red')).toBe('Error de red');
    expect(renderErrorDetail('   ', 'Error de red')).toBe('Error de red');
  });
});
