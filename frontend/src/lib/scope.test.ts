import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import type { DocumentInfo } from '../api/types';
import { findMissingDocuments, MAX_SCOPE_DOCUMENTS, missingScopeMessage, scopeFull, toggleScoped, type ScopedDocument } from './scope';

function scoped(count: number): ScopedDocument[] {
  return Array.from({ length: count }, (_, i) => ({ id: `d${i}`, filename: `file${i}.pdf` }));
}

describe('toggleScoped', () => {
  it('adds and removes documents', () => {
    const added = toggleScoped([], { id: 'a', filename: 'a.pdf' });
    expect(added).toEqual([{ id: 'a', filename: 'a.pdf' }]);
    expect(toggleScoped(added, { id: 'a', filename: 'a.pdf' })).toEqual([]);
  });

  it('keeps only id and filename of a full document', () => {
    const document = { id: 'a', filename: 'a.pdf', status: 'ready' } as DocumentInfo;
    expect(toggleScoped([], document)).toEqual([{ id: 'a', filename: 'a.pdf' }]);
  });

  it('refuses a document beyond the backend limit but still allows removal', () => {
    const full = scoped(MAX_SCOPE_DOCUMENTS);
    expect(MAX_SCOPE_DOCUMENTS).toBe(50);
    expect(scopeFull(full)).toBe(true);
    expect(scopeFull(scoped(49))).toBe(false);
    expect(toggleScoped(full, { id: 'extra', filename: 'extra.pdf' })).toBe(full);
    expect(toggleScoped(full, { id: 'd3', filename: 'file3.pdf' })).toHaveLength(49);
  });
});

describe('findMissingDocuments', () => {
  it('returns only documents the API reports as not found', async () => {
    const documents = scoped(3);
    const fetchDocument = async (id: string): Promise<DocumentInfo> => {
      if (id === 'd1') throw new ApiError(404, 'document_not_found', 'Không tìm thấy tài liệu.');
      if (id === 'd2') throw new ApiError(503, 'http_503', 'Dịch vụ tạm thời không sẵn sàng.');
      return { id } as DocumentInfo;
    };
    await expect(findMissingDocuments(documents, fetchDocument)).resolves.toEqual([{ id: 'd1', filename: 'file1.pdf' }]);
  });

  it('names the dropped files', () => {
    expect(missingScopeMessage(['a.pdf', 'b.pdf'])).toContain('a.pdf, b.pdf');
  });
});
