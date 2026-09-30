import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { keepsStreamingTurn, turnFailureMessage } from './useChat';

afterEach(() => vi.unstubAllGlobals());

const scope = [
  { id: 'gone', filename: 'da_xoa.pdf' },
  { id: 'kept', filename: 'con_lai.pdf' },
];

describe('keepsStreamingTurn', () => {
  it('keeps the answer when the open conversation is clicked again while streaming', () => {
    expect(keepsStreamingTurn('c1', 'c1', true)).toBe(true);
  });

  it('switches otherwise', () => {
    expect(keepsStreamingTurn('c1', 'c1', false)).toBe(false);
    expect(keepsStreamingTurn('c2', 'c1', true)).toBe(false);
    expect(keepsStreamingTurn('c1', null, true)).toBe(false);
  });
});

describe('turnFailureMessage', () => {
  it('drops scoped documents that no longer exist and names them', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) =>
        path.endsWith('/gone')
          ? new Response(JSON.stringify({ error: 'document_not_found', detail: 'Không tìm thấy tài liệu.' }), { status: 404 })
          : new Response(JSON.stringify({ id: 'kept' }), { status: 200 }),
      ),
    );
    const onScopeMissing = vi.fn();
    const refused = new ApiError(404, 'document_not_found', 'Không tìm thấy tài liệu trong phạm vi hỏi.');
    const message = await turnFailureMessage(refused, scope, onScopeMissing);
    expect(onScopeMissing).toHaveBeenCalledWith(['gone']);
    expect(message).toContain('da_xoa.pdf');
    expect(message).not.toContain('con_lai.pdf');
  });

  it('keeps the API message for other failures', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    const onScopeMissing = vi.fn();
    const message = await turnFailureMessage(new ApiError(503, 'llm_unavailable', 'Mô hình ngôn ngữ chưa sẵn sàng.'), scope, onScopeMissing);
    expect(message).toBe('Mô hình ngôn ngữ chưa sẵn sàng.');
    expect(await turnFailureMessage(new ApiError(404, 'document_not_found', 'x'), [], onScopeMissing)).toBe('x');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(onScopeMissing).not.toHaveBeenCalled();
  });

  it('keeps the API message when every scoped document still exists', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ id: 'x' }), { status: 200 })));
    const onScopeMissing = vi.fn();
    const refused = new ApiError(404, 'document_not_found', 'Không tìm thấy tài liệu trong phạm vi hỏi.');
    expect(await turnFailureMessage(refused, scope, onScopeMissing)).toBe('Không tìm thấy tài liệu trong phạm vi hỏi.');
    expect(onScopeMissing).not.toHaveBeenCalled();
  });
});
