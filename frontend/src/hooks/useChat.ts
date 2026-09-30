import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, getConversation, getDocument, isAbortError, streamChat } from '../api/client';
import type { ChatEvent, Source } from '../api/types';
import { summaryProgressText } from '../lib/format';
import { findMissingDocuments, missingScopeMessage, type ScopedDocument } from '../lib/scope';

export type MessageState = 'streaming' | 'done' | 'stopped' | 'error';

export interface ChatMessage {
  key: string;
  role: 'user' | 'assistant';
  content: string;
  sources: Source[];
  state: MessageState;
  error: string | null;
}

export interface Chat {
  messages: ChatMessage[];
  conversationId: string | null;
  streaming: boolean;
  /** Progress line while an answer is being prepared (search, summary parts). */
  status: string | null;
  loadingConversation: boolean;
  loadError: string | null;
  /** `scope`: the documents the question is restricted to; empty means all visible documents. */
  send: (question: string, scope: ScopedDocument[]) => void;
  stop: () => void;
  open: (conversationId: string) => void;
  reset: () => void;
}

let nextKey = 0;
function newKey(): string {
  nextKey += 1;
  return `msg-${nextKey}`;
}

/** Re-opening the conversation whose answer is streaming would abort that answer: keep it instead. */
export function keepsStreamingTurn(target: string, current: string | null, streaming: boolean): boolean {
  return streaming && target === current;
}

export async function turnFailureMessage(
  caught: unknown,
  scope: ScopedDocument[],
  onScopeMissing: (ids: string[]) => void,
): Promise<string> {
  const message = caught instanceof Error ? caught.message : String(caught);
  // The whole question is refused when one scoped document was deleted or unshared elsewhere: find and drop it.
  if (!(caught instanceof ApiError && caught.code === 'document_not_found' && scope.length)) return message;
  const missing = await findMissingDocuments(scope, getDocument);
  if (!missing.length) return message;
  onScopeMissing(missing.map((document) => document.id));
  return missingScopeMessage(missing.map((document) => document.filename));
}

/**
 * Chat state for one conversation: streams answers from POST /api/chat and loads stored conversations.
 * `onSaved` runs after every turn (the question is stored even when the answer fails or is stopped);
 * `onScopeMissing` receives scoped document ids that no longer exist for this user.
 */
export function useChat(onSaved: () => void, onScopeMissing: (ids: string[]) => void): Chat {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const conversationRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Incremented whenever the visible conversation changes, so late responses of an older one are dropped.
  const generation = useRef(0);
  const savedRef = useRef(onSaved);
  const scopeMissingRef = useRef(onScopeMissing);

  useEffect(() => {
    savedRef.current = onSaved;
    scopeMissingRef.current = onScopeMissing;
  }, [onSaved, onScopeMissing]);

  const selectConversation = useCallback((id: string | null) => {
    conversationRef.current = id;
    setConversationId(id);
  }, []);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const send = useCallback(
    (question: string, scope: ScopedDocument[]) => {
      if (abortRef.current) return;
      const controller = new AbortController();
      abortRef.current = controller;
      const current = generation.current;
      const answerKey = newKey();
      const patch = (change: (message: ChatMessage) => Partial<ChatMessage>) => {
        if (current !== generation.current) return;
        setMessages((list) => list.map((message) => (message.key === answerKey ? { ...message, ...change(message) } : message)));
      };
      setMessages((list) => [
        ...list,
        { key: newKey(), role: 'user', content: question, sources: [], state: 'done', error: null },
        { key: answerKey, role: 'assistant', content: '', sources: [], state: 'streaming', error: null },
      ]);
      setStreaming(true);
      setStatus('Đang tìm thông tin trong tài liệu…');

      const handle = (event: ChatEvent) => {
        if (current !== generation.current) return;
        switch (event.kind) {
          case 'meta':
            if (event.data.conversation_id) selectConversation(event.data.conversation_id);
            if (event.data.intent === 'summary') setStatus('Đang chuẩn bị tóm tắt…');
            else if (event.data.intent === 'qa') setStatus('Đang soạn câu trả lời…');
            break;
          case 'progress':
            setStatus(summaryProgressText(event.data));
            break;
          case 'token':
            setStatus(null);
            patch((message) => ({ content: message.content + event.data.text }));
            break;
          case 'replace':
            patch(() => ({ content: event.data.text }));
            break;
          case 'done':
            patch(() => ({ content: event.data.answer, sources: event.data.sources, state: 'done' }));
            break;
          case 'error':
            patch(() => ({ state: 'error', error: event.data.detail }));
            break;
        }
      };

      const body = {
        question,
        ...(conversationRef.current ? { conversation_id: conversationRef.current } : {}),
        ...(scope.length ? { document_ids: scope.map((document) => document.id) } : {}),
      };
      streamChat(body, handle, controller.signal)
        .then(() =>
          patch((message) =>
            message.state === 'streaming'
              ? { state: 'error', error: 'Kết nối bị ngắt trước khi có câu trả lời hoàn chỉnh.' }
              : {},
          ),
        )
        .catch(async (caught: unknown) => {
          if (isAbortError(caught)) patch(() => ({ state: 'stopped' }));
          else {
            const error = await turnFailureMessage(caught, scope, scopeMissingRef.current);
            patch(() => ({ state: 'error', error }));
          }
        })
        .finally(() => {
          // The server stores the question before answering, so the history changes whatever the outcome.
          savedRef.current();
          if (abortRef.current === controller) abortRef.current = null;
          if (current === generation.current) {
            setStreaming(false);
            setStatus(null);
          }
        });
    },
    [selectConversation],
  );

  const switchTo = useCallback(
    (id: string | null) => {
      abortRef.current?.abort();
      abortRef.current = null;
      generation.current += 1;
      setStreaming(false);
      setStatus(null);
      setLoadError(null);
      setMessages([]);
      selectConversation(id);
      return generation.current;
    },
    [selectConversation],
  );

  const reset = useCallback(() => {
    switchTo(null);
    setLoadingConversation(false);
  }, [switchTo]);

  const open = useCallback(
    (id: string) => {
      if (keepsStreamingTurn(id, conversationRef.current, abortRef.current !== null)) return;
      const current = switchTo(id);
      setLoadingConversation(true);
      getConversation(id)
        .then((detail) => {
          if (current !== generation.current) return;
          setMessages(
            detail.messages.map((message) => ({
              key: message.id,
              role: message.role,
              content: message.content,
              sources: message.sources ?? [],
              state: 'done',
              error: null,
            })),
          );
        })
        .catch((caught: unknown) => {
          if (current === generation.current) setLoadError(caught instanceof Error ? caught.message : String(caught));
        })
        .finally(() => {
          if (current === generation.current) setLoadingConversation(false);
        });
    },
    [switchTo],
  );

  useEffect(() => () => abortRef.current?.abort(), []);

  return { messages, conversationId, streaming, status, loadingConversation, loadError, send, stop, open, reset };
}
