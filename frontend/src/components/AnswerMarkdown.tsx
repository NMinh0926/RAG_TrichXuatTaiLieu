import { memo, useMemo, type ReactNode } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Source } from '../api/types';
import { linkifyCitations, parseCitationHref } from '../lib/citations';
import { sourceLocation } from '../lib/format';
import { linkAddressNote } from '../lib/links';

function textOf(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  return Array.isArray(node) ? node.map(textOf).join('') : '';
}

interface AnswerMarkdownProps {
  content: string;
  sources: Source[];
  onCite: (source: Source) => void;
}

/** Markdown answer (GFM) whose [n] markers become buttons opening the cited source; other links stay plain text. */
export const AnswerMarkdown = memo(function AnswerMarkdown({ content, sources, onCite }: AnswerMarkdownProps) {
  const sourceCount = sources.reduce((max, source) => Math.max(max, source.n), 0);
  const markdown = useMemo(() => linkifyCitations(content, sourceCount), [content, sourceCount]);

  const components = useMemo<Components>(
    () => ({
      a({ href, children }) {
        const n = parseCitationHref(href);
        if (n === null) {
          const note = linkAddressNote(href, textOf(children));
          return (
            <span className="break-words" title="Liên kết trong câu trả lời không được mở trực tiếp">
              {children}
              {note && <span className="break-all text-slate-500 dark:text-slate-400"> ({note})</span>}
            </span>
          );
        }
        const source = sources.find((item) => item.n === n);
        const label = source ? `Nguồn ${n}: ${source.filename ?? 'không rõ'}, ${sourceLocation(source)}` : `Nguồn ${n}`;
        return (
          <button
            type="button"
            onClick={() => source && onCite(source)}
            disabled={!source?.document_id}
            title={label}
            aria-label={`Mở ${label}`}
            className="mx-0.5 inline-flex min-w-5 -translate-y-px items-center justify-center rounded-md bg-brand-100 px-1.5 align-baseline text-xs leading-5 font-semibold text-brand-700 no-underline transition hover:bg-brand-500 hover:text-white disabled:cursor-default disabled:opacity-60 dark:bg-brand-700/30 dark:text-brand-100"
          >
            {n}
          </button>
        );
      },
      // Images would be fetched automatically (with the session cookie for same-origin URLs): show them as text.
      img({ src, alt }) {
        const address = typeof src === 'string' ? src : '';
        return (
          <span className="break-all text-slate-500 dark:text-slate-400">
            [Hình: {alt || 'không có mô tả'}
            {address && ` (${address})`}]
          </span>
        );
      },
      table({ children }) {
        return (
          <div className="overflow-x-auto">
            <table>{children}</table>
          </div>
        );
      },
    }),
    [sources, onCite],
  );

  return (
    <div className="markdown">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>
        {markdown}
      </Markdown>
    </div>
  );
});
