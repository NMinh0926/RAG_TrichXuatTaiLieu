import { describe, expect, it } from 'vitest';
import { carriesFiles } from './dragdrop';

describe('carriesFiles', () => {
  it('detects files dragged from the operating system', () => {
    expect(carriesFiles({ types: ['Files', 'application/x-moz-file'] })).toBe(true);
  });

  it('ignores text, links and missing data', () => {
    expect(carriesFiles({ types: ['text/plain', 'text/uri-list'] })).toBe(false);
    expect(carriesFiles(null)).toBe(false);
  });
});
