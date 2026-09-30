import { memo, useEffect, useState } from 'react';
import type { Source } from '../api/types';
import type { ChatMessage } from '../hooks/useChat';
import { sourceLocation } from '../lib/format';
import { AnswerMarkdown } from './AnswerMarkdown';
import { Icon } from './Icon';
import { ErrorBanner } from './ui';

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');

  useEffect(() => {
    if (state === 'idle') return;
    const timer = setTimeout(() => setState('idle'), 2000);
    return () => clearTimeout(timer);
  }, [state]);

  const copy = () => {
    navigator.clipboard.writeText(text).then(
      () => setState('copied'),
      () => setState('failed'),
    );
  };

  const label = state === 'copied' ? 'Đã sao chép' : state === 'failed' ? 'Không sao chép được' : 'Sao chép';
  return (
    <button
      type="button"
      onClick={copy}
      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-500 transition hover:bg-slate-200 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
    >
      <Icon name={state === 'copied' ? 'check' : 'copy'} className="size-3.5" />
      <span aria-live="polite">{label}</span>
    </button>
  );
}

function SourceList({ sources, onOpen }: { sources: Source[]; onOpen: (source: Source) => void }) {
  return (
    <section aria-label="Nguồn tham khảo" className="mt-3 border-t border-slate-200 pt-2 dark:border-slate-800">
      <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">Nguồn ({sources.length})</h3>
      <ol className="space-y-1.5">
        {sources.map((source) => (
          <li key={source.n}>
            <button
              type="button"
              onClick={() => onOpen(source)}
              disabled={!source.document_id}
              className="flex w-full gap-2 rounded-lg p-2 text-left text-sm transition hover:bg-slate-100 disabled:cursor-default disabled:hover:bg-transparent dark:hover:bg-slate-800/70"
            >
              <span className="flex size-5 shrink-0 items-center justify-center rounded-md bg-brand-100 text-xs font-semibold text-brand-700 dark:bg-brand-700/30 dark:text-brand-100">
                {source.n}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex min-w-0 gap-1 font-medium">
                  <span className="truncate" title={source.filename ?? undefined}>
                    {source.filename ?? 'Không rõ tài liệu'}
                  </span>
                  <span className="shrink-0 font-normal text-slate-500 dark:text-slate-400">· {sourceLocation(source)}</span>
                </span>
                {source.excerpt && <span className="mt-0.5 line-clamp-2 block text-xs text-slate-600 dark:text-slate-400">{source.excerpt}</span>}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

interface ChatMessageViewProps {
  message: ChatMessage;
  onOpenSource: (source: Source) => void;
}

export const ChatMessageView = memo(function ChatMessageView({ message, onOpenSource }: ChatMessageViewProps) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 whitespace-pre-wrap text-white">{message.content}</div>
      </div>
    );
  }

  const streaming = message.state === 'streaming';
  return (
    <article aria-label="Câu trả lời" aria-busy={streaming} className="rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      {message.content ? (
        <AnswerMarkdown content={message.content} sources={message.sources} onCite={onOpenSource} />
      ) : (
        streaming && (
          <div className="flex gap-1 py-2" aria-label="Đang trả lời">
            {[0, 150, 300].map((delay) => (
              <span key={delay} className="size-2 animate-bounce rounded-full bg-slate-400" style={{ animationDelay: `${delay}ms` }} />
            ))}
          </div>
        )
      )}
      {message.state === 'stopped' && <p className="mt-2 text-xs text-slate-500 italic dark:text-slate-400">Đã dừng. Câu hỏi vẫn có trong lịch sử, nhưng câu trả lời chưa đầy đủ này không được lưu.</p>}
      {message.state === 'error' && message.error && (
        <div className="mt-2">
          <ErrorBanner message={message.error} />
        </div>
      )}
      {message.sources.length > 0 && <SourceList sources={message.sources} onOpen={onOpenSource} />}
      {message.state === 'done' && message.content && (
        <div className="mt-2 flex justify-end">
          <CopyButton text={message.content} />
        </div>
      )}
    </article>
  );
});
