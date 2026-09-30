import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Source } from '../api/types';
import { AnswerMarkdown } from './AnswerMarkdown';

const source: Source = {
  n: 1,
  chunk_id: 'c1',
  document_id: 'd1',
  filename: 'quy_che.pdf',
  page: 3,
  sheet: null,
  excerpt: null,
  bboxes: null,
};

function render(content: string, sources: Source[] = []): string {
  return renderToStaticMarkup(createElement(AnswerMarkdown, { content, sources, onCite: () => undefined }));
}

describe('AnswerMarkdown', () => {
  it('renders an injected markdown link as text with its address, not as a link', () => {
    const html = render('Nghỉ phép 12 ngày. [Xem chi tiết](https://evil.example/?d=bi-mat)');
    expect(html).not.toContain('<a');
    expect(html).not.toContain('href=');
    expect(html).toContain('Xem chi tiết');
    expect(html).toContain('(https://evil.example/?d=bi-mat)');
  });

  it('does not turn bare URLs into links', () => {
    const html = render('Tham khảo https://evil.example/login ngay.');
    expect(html).not.toContain('<a');
    expect(html).toContain('https://evil.example/login');
  });

  it('does not load images from the answer', () => {
    const html = render('![logo](https://evil.example/pixel.png?d=bi-mat)');
    expect(html).not.toContain('<img');
    expect(html).toContain('[Hình: logo (https://evil.example/pixel.png?d=bi-mat)]');
  });

  it('keeps citation markers as buttons', () => {
    const html = render('Được nghỉ 12 ngày [1].', [source]);
    expect(html).toContain('<button');
    expect(html).toContain('Nguồn 1: quy_che.pdf, trang 3');
  });
});
