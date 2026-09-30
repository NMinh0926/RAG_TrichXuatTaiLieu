import { describe, expect, it } from 'vitest';
import { linkAddressNote } from './links';

describe('linkAddressNote', () => {
  it('reveals where a labelled link points', () => {
    expect(linkAddressNote('https://evil.example/?d=secret', 'Xem chi tiết')).toBe('https://evil.example/?d=secret');
  });

  it('adds nothing when the text already shows the address', () => {
    expect(linkAddressNote('https://example.com', 'https://example.com')).toBeNull();
    expect(linkAddressNote('http://www.example.com', 'www.example.com')).toBeNull();
    expect(linkAddressNote('mailto:an@example.com', 'an@example.com')).toBeNull();
    expect(linkAddressNote(undefined, 'x')).toBeNull();
  });
});
