import { useCallback } from 'react';
import { deleteConversation } from '../api/client';
import type { Conversation } from '../api/types';
import { useConfirmAction } from '../hooks/useConfirmAction';
import type { PagedList } from '../hooks/usePagedList';
import { formatDate } from '../lib/format';
import { Icon } from './Icon';
import { ConfirmDialog, EmptyState, ErrorBanner, iconButton, ListSkeleton, secondaryButton, Spinner } from './ui';

interface ConversationHistoryProps {
  conversations: PagedList<Conversation>;
  activeId: string | null;
  onOpen: (id: string) => void;
  onDeleted: (id: string) => void;
}

export function ConversationHistory({ conversations, activeId, onOpen, onDeleted }: ConversationHistoryProps) {
  const { remove } = conversations;
  const removal = useConfirmAction(
    (conversation: Conversation) => deleteConversation(conversation.id),
    useCallback(
      (conversation: Conversation) => {
        remove(conversation.id);
        onDeleted(conversation.id);
      },
      [remove, onDeleted],
    ),
  );

  return (
    <div className="flex h-full flex-col gap-3">
      {conversations.error && <ErrorBanner message={conversations.error} onRetry={conversations.refresh} />}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {conversations.loading ? (
          <ListSkeleton label="Đang tải lịch sử trò chuyện" />
        ) : conversations.items.length === 0 ? (
          !conversations.error && (
            <EmptyState icon="chat" title="Chưa có cuộc trò chuyện">
              Các câu hỏi bạn đặt sẽ được lưu ở đây.
            </EmptyState>
          )
        ) : (
          <>
            <ul className="space-y-1" aria-label="Lịch sử trò chuyện">
              {conversations.items.map((conversation) => {
                const active = conversation.id === activeId;
                return (
                  <li
                    key={conversation.id}
                    className={`flex items-center gap-1 rounded-lg border p-2 transition ${
                      active
                        ? 'border-brand-500 bg-brand-50 dark:bg-brand-700/15'
                        : 'border-transparent hover:border-slate-200 hover:bg-white dark:hover:border-slate-800 dark:hover:bg-slate-900'
                    }`}
                  >
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      onClick={() => onOpen(conversation.id)}
                      aria-current={active ? 'true' : undefined}
                    >
                      <span className="block truncate text-sm font-medium" title={conversation.title}>
                        {conversation.title || 'Cuộc trò chuyện'}
                      </span>
                      <span className="block text-xs text-slate-500 dark:text-slate-400">{formatDate(conversation.updated_at)}</span>
                    </button>
                    <button
                      type="button"
                      className={`${iconButton} size-8 hover:text-red-600 dark:hover:text-red-400`}
                      onClick={() => removal.ask(conversation)}
                      aria-label={`Xóa cuộc trò chuyện ${conversation.title}`}
                      title="Xóa"
                    >
                      <Icon name="trash" className="size-4" />
                    </button>
                  </li>
                );
              })}
            </ul>
            {conversations.items.length < conversations.total && (
              <button
                type="button"
                className={`${secondaryButton} mt-2 w-full`}
                onClick={conversations.loadMore}
                disabled={conversations.loadingMore}
              >
                {conversations.loadingMore && <Spinner />}
                Tải thêm
              </button>
            )}
          </>
        )}
      </div>
      <ConfirmDialog
        open={removal.target !== null}
        title="Xóa cuộc trò chuyện?"
        message={
          <>
            Cuộc trò chuyện <strong>{removal.target?.title}</strong> và toàn bộ tin nhắn sẽ bị xóa.
          </>
        }
        confirmLabel="Xóa"
        busy={removal.busy}
        error={removal.error}
        onConfirm={removal.confirm}
        onCancel={removal.cancel}
      />
    </div>
  );
}
