import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ChatMessage } from '../hooks/useChat';
import { ChatMessageView } from './ChatMessageView';

const longName = 'Bao_cao_tai_chinh_quy_1_nam_2024_ban_chinh_thuc_da_duyet.pdf';

function render(message: Partial<ChatMessage>): string {
  const full: ChatMessage = { key: 'm1', role: 'assistant', content: 'Trả lời [1].', sources: [], state: 'done', error: null, ...message };
  return renderToStaticMarkup(createElement(ChatMessageView, { message: full, onOpenSource: () => undefined }));
}

describe('ChatMessageView sources', () => {
  it('keeps the page label outside the truncated filename and shows the full name on hover', () => {
    const html = render({
      sources: [{ n: 1, chunk_id: 'c', document_id: 'd', filename: longName, page: 7, sheet: null, excerpt: null, bboxes: null }],
    });
    expect(html).toContain(`<span class="truncate" title="${longName}">${longName}</span>`);
    expect(html).toMatch(/<span class="shrink-0[^"]*">· trang 7<\/span>/);
  });

  it('says that a stopped question stays in the history', () => {
    expect(render({ state: 'stopped' })).toContain('Câu hỏi vẫn có trong lịch sử');
  });
});
