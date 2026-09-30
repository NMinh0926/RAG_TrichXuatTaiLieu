import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import type { Source } from '../api/types';
import type { Chat } from '../hooks/useChat';
import type { ScopedDocument } from '../lib/scope';
import { ChatMessageView } from './ChatMessageView';
import { Icon } from './Icon';
import { ScopeSelector } from './ScopeSelector';
import { ErrorBanner, Skeleton, Spinner } from './ui';

const QUICK_PROMPTS = ['Tóm tắt tài liệu', 'Liệt kê ý chính'];
const MAX_QUESTION = 4000;

export interface Scope {
  selectAll: boolean;
  selected: ScopedDocument[];
}

interface ChatPanelProps {
  chat: Chat;
  scope: Scope;
  onScopeChange: (scope: Scope) => void;
  onOpenSource: (source: Source) => void;
}

export function ChatPanel({ chat, scope, onScopeChange, onOpenSource }: ChatPanelProps) {
  const [draft, setDraft] = useState('');
  const scroller = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const stickToBottom = useRef(true);
  const scopeMissing = !scope.selectAll && scope.selected.length === 0;
  const canSend = !chat.streaming && !chat.loadingConversation && !scopeMissing;

  // Follow the streamed answer unless the user scrolled up to read.
  useEffect(() => {
    const element = scroller.current;
    if (element && stickToBottom.current) element.scrollTop = element.scrollHeight;
  }, [chat.messages, chat.status]);

  useEffect(() => {
    const element = textarea.current;
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }, [draft]);

  const ask = (question: string) => {
    const text = question.trim();
    if (!text || !canSend) return;
    stickToBottom.current = true;
    chat.send(text, scope.selectAll ? [] : scope.selected);
    setDraft('');
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    ask(draft);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      ask(draft);
    }
  };

  const quickPrompts = (
    <div className="flex flex-wrap gap-2">
      {QUICK_PROMPTS.map((prompt) => (
        <button
          key={prompt}
          type="button"
          disabled={!canSend}
          onClick={() => ask(prompt)}
          className="rounded-full border border-slate-300 bg-white px-3 py-1 text-sm transition hover:border-brand-500 hover:text-brand-700 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:text-brand-500"
        >
          {prompt}
        </button>
      ))}
    </div>
  );

  return (
    <section aria-label="Trò chuyện" className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div
        ref={scroller}
        onScroll={(event) => {
          const element = event.currentTarget;
          stickToBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
        }}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
          {chat.loadError && <ErrorBanner message={chat.loadError} />}
          {chat.loadingConversation ? (
            <div role="status" aria-label="Đang tải cuộc trò chuyện" className="space-y-4">
              <Skeleton className="ml-auto h-10 w-2/3" />
              <Skeleton className="h-28 w-full" />
              <Skeleton className="ml-auto h-10 w-1/2" />
            </div>
          ) : chat.messages.length === 0 ? (
            !chat.loadError && (
              <div className="flex flex-col items-center gap-4 py-16 text-center">
                <span className="flex size-12 items-center justify-center rounded-2xl bg-brand-100 text-brand-700 dark:bg-brand-700/30 dark:text-brand-100">
                  <Icon name="chat" className="size-6" />
                </span>
                <div>
                  <h2 className="text-lg font-semibold">Hãy đặt câu hỏi về tài liệu của bạn</h2>
                  <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                    Câu trả lời chỉ dựa trên tài liệu đã tải lên, kèm trích dẫn để mở đúng trang.
                  </p>
                </div>
                {quickPrompts}
              </div>
            )
          ) : (
            <div role="log" aria-label="Tin nhắn" className="flex flex-col gap-4">
              {chat.messages.map((message) => (
                <ChatMessageView key={message.key} message={message} onOpenSource={onOpenSource} />
              ))}
            </div>
          )}
          <p aria-live="polite" className="flex min-h-5 items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
            {chat.status && (
              <>
                <Spinner className="size-3" />
                {chat.status}
              </>
            )}
          </p>
        </div>
      </div>
      <form onSubmit={onSubmit} className="border-t border-slate-200 bg-slate-50/80 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
        <div className="mx-auto flex max-w-3xl flex-col gap-2 px-4 py-3">
          <ScopeSelector
            selectAll={scope.selectAll}
            selected={scope.selected}
            disabled={chat.streaming}
            onChange={(selectAll, selected) => onScopeChange({ selectAll, selected })}
          />
          {scopeMissing && <p className="text-xs text-amber-700 dark:text-amber-400">Hãy chọn ít nhất một tài liệu, hoặc hỏi trên tất cả tài liệu.</p>}
          {chat.messages.length > 0 && quickPrompts}
          <div className="flex items-end gap-2 rounded-xl border border-slate-300 bg-white p-2 focus-within:border-brand-500 dark:border-slate-700 dark:bg-slate-900">
            <textarea
              ref={textarea}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              rows={1}
              maxLength={MAX_QUESTION}
              disabled={chat.loadingConversation}
              placeholder="Hãy đặt câu hỏi… (Enter để gửi, Shift+Enter để xuống dòng)"
              aria-label="Câu hỏi"
              className="max-h-50 min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 outline-none focus-visible:outline-none"
            />
            {chat.streaming ? (
              <button
                type="button"
                onClick={chat.stop}
                className="inline-flex size-9 items-center justify-center rounded-lg bg-slate-800 text-white hover:bg-slate-700 dark:bg-slate-200 dark:text-slate-900"
                aria-label="Dừng trả lời"
                title="Dừng"
              >
                <Icon name="stop" className="size-4" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!canSend || !draft.trim()}
                className="inline-flex size-9 items-center justify-center rounded-lg bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-40"
                aria-label="Gửi câu hỏi"
                title="Gửi"
              >
                <Icon name="send" className="size-4" />
              </button>
            )}
          </div>
          {draft.length > MAX_QUESTION - 500 && (
            <p className="text-right text-xs text-slate-500">
              {draft.length}/{MAX_QUESTION} ký tự
            </p>
          )}
        </div>
      </form>
    </section>
  );
}
