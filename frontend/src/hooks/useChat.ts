import { useCallback, useEffect, useRef, useState } from 'react';
import { getConversation, isAbortError, streamChat } from '../api/client';
import type { ChatEvent, Source } from '../api/types';
import { summaryProgressText } from '../lib/format';

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
  send: (question: string, documentIds: string[]) => void;
  stop: () => void;
  open: (conversationId: string) => void;
  reset: () => void;
}

let nextKey = 0;
function newKey(): string {
  nextKey += 1;
  return `msg-${nextKey}`;
}

/** Chat state for one conversation: streams answers from POST /api/chat and loads stored conversations. */
export function useChat(onSaved: () => void): Chat {
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

  useEffect(() => {
    savedRef.current = onSaved;
  }, [onSaved]);

  const selectConversation = useCallback((id: string | null) => {
    conversationRef.current = id;
    setConversationId(id);
  }, []);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const send = useCallback(
    (question: string, documentIds: string[]) => {
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
            savedRef.current();
            break;
          case 'error':
            patch(() => ({ state: 'error', error: event.data.detail }));
            break;
        }
      };

      const body = {
        question,
        ...(conversationRef.current ? { conversation_id: conversationRef.current } : {}),
        ...(documentIds.length ? { document_ids: documentIds } : {}),
      };
      streamChat(body, handle, controller.signal)
        .then(() =>
          patch((message) =>
            message.state === 'streaming'
              ? { state: 'error', error: 'Kết nối bị ngắt trước khi có câu trả lời hoàn chỉnh.' }
              : {},
          ),
        )
        .catch((caught: unknown) => {
          if (isAbortError(caught)) patch(() => ({ state: 'stopped' }));
          else patch(() => ({ state: 'error', error: caught instanceof Error ? caught.message : String(caught) }));
        })
        .finally(() => {
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
