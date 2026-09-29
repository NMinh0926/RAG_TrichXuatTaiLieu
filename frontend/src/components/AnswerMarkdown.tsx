import { memo, useMemo } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Source } from '../api/types';
import { linkifyCitations, parseCitationHref } from '../lib/citations';
import { sourceLocation } from '../lib/format';

interface AnswerMarkdownProps {
  content: string;
  sources: Source[];
  onCite: (source: Source) => void;
}

/** Markdown answer (GFM) whose [n] markers become buttons opening the cited source. */
export const AnswerMarkdown = memo(function AnswerMarkdown({ content, sources, onCite }: AnswerMarkdownProps) {
  const sourceCount = sources.reduce((max, source) => Math.max(max, source.n), 0);
  const markdown = useMemo(() => linkifyCitations(content, sourceCount), [content, sourceCount]);

  const components = useMemo<Components>(
    () => ({
      a({ href, children }) {
        const n = parseCitationHref(href);
        if (n === null) {
          return (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
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
