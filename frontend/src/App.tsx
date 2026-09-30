import { useCallback, useRef, useState } from 'react';
import { listConversations } from './api/client';
import type { DocumentInfo, Source, User } from './api/types';
import { ChatPanel, type Scope } from './components/ChatPanel';
import { ConversationHistory } from './components/ConversationHistory';
import { DocumentLibrary } from './components/DocumentLibrary';
import { Icon } from './components/Icon';
import { LlmStatus } from './components/LlmStatus';
import { Sidebar } from './components/Sidebar';
import { iconButton } from './components/ui';
import { UserMenu } from './components/UserMenu';
import { ViewerPanel, type ViewerTarget } from './components/ViewerPanel';
import { useChat } from './hooks/useChat';
import { DESKTOP_QUERY, useMediaQuery } from './hooks/useMediaQuery';
import { usePagedList } from './hooks/usePagedList';
import { useTheme } from './hooks/useTheme';

const CONVERSATION_PAGE_SIZE = 20;

export function App({ user, onLogout }: { user: User; onLogout: () => void }) {
  const { theme, toggle: toggleTheme } = useTheme();
  const desktop = useMediaQuery(DESKTOP_QUERY);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [viewer, setViewer] = useState<ViewerTarget | null>(null);
  const [scope, setScope] = useState<Scope>({ selectAll: true, selected: [] });
  const conversations = usePagedList(listConversations, CONVERSATION_PAGE_SIZE);
  const chat = useChat(conversations.refresh);
  const { conversationId, reset: resetChat, open: openConversation } = chat;
  const nonce = useRef(0);

  const closeDrawer = useCallback(() => {
    if (!desktop) setSidebarOpen(false);
  }, [desktop]);

  const openViewer = useCallback(
    (target: Omit<ViewerTarget, 'nonce'>) => {
      nonce.current += 1;
      setViewer({ ...target, nonce: nonce.current });
      closeDrawer();
    },
    [closeDrawer],
  );

  const openSource = useCallback(
    (source: Source) => {
      if (!source.document_id) return;
      openViewer({
        documentId: source.document_id,
        filename: source.filename ?? 'Tài liệu',
        page: source.page,
        sheet: source.sheet,
        bboxes: source.bboxes ?? [],
      });
    },
    [openViewer],
  );

  const openDocument = useCallback(
    (document: DocumentInfo) => openViewer({ documentId: document.id, filename: document.filename, page: null, sheet: null, bboxes: [] }),
    [openViewer],
  );

  const onDocumentDeleted = useCallback((id: string) => {
    setScope((current) => ({ ...current, selected: current.selected.filter((document) => document.id !== id) }));
    setViewer((current) => (current?.documentId === id ? null : current));
  }, []);

  const onConversationDeleted = useCallback(
    (id: string) => {
      if (conversationId === id) resetChat();
    },
    [conversationId, resetChat],
  );

  const onOpenConversation = useCallback(
    (id: string) => {
      openConversation(id);
      closeDrawer();
    },
    [openConversation, closeDrawer],
  );

  const onNewChat = useCallback(() => {
    resetChat();
    closeDrawer();
  }, [resetChat, closeDrawer]);

  const closeViewer = useCallback(() => setViewer(null), []);

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 dark:border-slate-800 dark:bg-slate-900">
        <button
          type="button"
          className={`${iconButton} lg:hidden`}
          onClick={() => setSidebarOpen(true)}
          aria-label="Mở thanh bên"
          aria-expanded={sidebarOpen}
        >
          <Icon name="menu" />
        </button>
        <span className="flex size-8 items-center justify-center rounded-lg bg-brand-600 text-white" aria-hidden="true">
          <Icon name="file" className="size-4" />
        </span>
        <h1 className="flex-1 truncate font-semibold">Trợ lý tài liệu</h1>
        <LlmStatus />
        <button
          type="button"
          className={iconButton}
          onClick={toggleTheme}
          aria-label={theme === 'dark' ? 'Chuyển sang giao diện sáng' : 'Chuyển sang giao diện tối'}
          title={theme === 'dark' ? 'Giao diện sáng' : 'Giao diện tối'}
        >
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
        </button>
        <UserMenu user={user} onLogout={onLogout} />
      </header>
      <div className="flex min-h-0 flex-1">
        <Sidebar
          open={sidebarOpen}
          docked={desktop}
          onClose={() => setSidebarOpen(false)}
          onNewChat={onNewChat}
          documents={<DocumentLibrary activeDocumentId={viewer?.documentId ?? null} onOpen={openDocument} onDeleted={onDocumentDeleted} />}
          history={
            <ConversationHistory
              conversations={conversations}
              activeId={conversationId}
              onOpen={onOpenConversation}
              onDeleted={onConversationDeleted}
            />
          }
        />
        <main className="flex min-w-0 flex-1 flex-col">
          <ChatPanel chat={chat} scope={scope} onScopeChange={setScope} onOpenSource={openSource} />
        </main>
        {viewer && <ViewerPanel target={viewer} onClose={closeViewer} />}
      </div>
    </div>
  );
}
