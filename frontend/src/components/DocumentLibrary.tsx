import { useCallback, useEffect, useState } from 'react';
import { deleteDocument, listDocuments, reindexDocument } from '../api/client';
import type { DocumentInfo } from '../api/types';
import { useConfirmAction } from '../hooks/useConfirmAction';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { usePagedList } from '../hooks/usePagedList';
import { useUploads } from '../hooks/useUploads';
import { formatBytes, formatDate, STEP_LABELS } from '../lib/format';
import { Icon } from './Icon';
import { UploadPanel } from './UploadPanel';
import { ConfirmDialog, EmptyState, ErrorBanner, iconButton, ListSkeleton, secondaryButton, Spinner, StatusBadge } from './ui';

const PAGE_SIZE = 20;
const POLL_MS = 4000;

interface DocumentLibraryProps {
  activeDocumentId: string | null;
  onOpen: (document: DocumentInfo) => void;
  onDeleted: (id: string) => void;
}

function DocumentRow({
  document,
  active,
  onOpen,
  onDelete,
  onReindex,
}: {
  document: DocumentInfo;
  active: boolean;
  onOpen: () => void;
  onDelete: () => void;
  onReindex: () => void;
}) {
  const busy = document.status === 'pending' || document.status === 'processing';
  const details = [formatBytes(document.size_bytes), document.page_count ? `${document.page_count} trang` : null, formatDate(document.created_at)]
    .filter(Boolean)
    .join(' · ');
  return (
    <li
      className={`group flex items-start gap-1 rounded-lg border p-2 transition ${
        active
          ? 'border-brand-500 bg-brand-50 dark:bg-brand-700/15'
          : 'border-transparent hover:border-slate-200 hover:bg-white dark:hover:border-slate-800 dark:hover:bg-slate-900'
      }`}
    >
      <button type="button" onClick={onOpen} className="min-w-0 flex-1 text-left" aria-current={active ? 'true' : undefined}>
        <span className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-sm font-medium" title={document.filename}>
            {document.filename}
          </span>
          <StatusBadge status={document.status} />
        </span>
        <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">{details}</span>
        {busy && document.latest_job && (
          <span className="mt-0.5 block text-xs text-amber-700 dark:text-amber-400">
            {STEP_LABELS[document.latest_job.step]} · {document.latest_job.progress}%
          </span>
        )}
        {document.status === 'failed' && document.error_message && (
          <span className="mt-0.5 block text-xs text-red-700 dark:text-red-400">{document.error_message}</span>
        )}
      </button>
      {document.status === 'failed' && (
        <button type="button" className={`${iconButton} size-8`} onClick={onReindex} aria-label={`Xử lý lại ${document.filename}`} title="Xử lý lại">
          <Icon name="refresh" className="size-4" />
        </button>
      )}
      <button
        type="button"
        className={`${iconButton} size-8 hover:text-red-600 dark:hover:text-red-400`}
        onClick={onDelete}
        aria-label={`Xóa ${document.filename}`}
        title="Xóa"
      >
        <Icon name="trash" className="size-4" />
      </button>
    </li>
  );
}

export function DocumentLibrary({ activeDocumentId, onOpen, onDeleted }: DocumentLibraryProps) {
  const [search, setSearch] = useState('');
  const q = useDebouncedValue(search.trim(), 300);
  const fetchPage = useCallback((page: number, pageSize: number) => listDocuments(page, pageSize, q), [q]);
  const documents = usePagedList(fetchPage, PAGE_SIZE);
  const { refresh, remove } = documents;
  const uploads = useUploads(refresh);
  const [actionError, setActionError] = useState<string | null>(null);

  const removal = useConfirmAction(
    (document: DocumentInfo) => deleteDocument(document.id),
    useCallback(
      (document: DocumentInfo) => {
        remove(document.id);
        onDeleted(document.id);
      },
      [remove, onDeleted],
    ),
  );

  // Documents processed outside this tab (or before a reload) have no event stream here: poll until settled.
  const hasBusy = documents.items.some((document) => document.status === 'pending' || document.status === 'processing');
  useEffect(() => {
    if (!hasBusy) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [hasBusy, refresh]);

  const reindex = (document: DocumentInfo) => {
    setActionError(null);
    reindexDocument(document.id)
      .then((job) => {
        uploads.track(job, document.filename);
        refresh();
      })
      .catch((caught: unknown) => setActionError(caught instanceof Error ? caught.message : String(caught)));
  };

  return (
    <div className="flex h-full flex-col gap-3">
      <UploadPanel uploads={uploads} />
      <div className="relative">
        <Icon name="search" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-slate-400" />
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Tìm theo tên file…"
          aria-label="Tìm tài liệu theo tên"
          className="w-full rounded-lg border border-slate-300 bg-white py-2 pr-3 pl-8 text-sm dark:border-slate-700 dark:bg-slate-900"
        />
      </div>
      {actionError && <ErrorBanner message={actionError} />}
      {documents.error && <ErrorBanner message={documents.error} onRetry={refresh} />}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {documents.loading ? (
          <ListSkeleton label="Đang tải danh sách tài liệu" />
        ) : documents.items.length === 0 ? (
          !documents.error && (
            <EmptyState icon="file" title={q ? 'Không tìm thấy tài liệu phù hợp' : 'Chưa có tài liệu'}>
              {q ? 'Thử từ khóa khác.' : 'Tải file lên để bắt đầu hỏi đáp.'}
            </EmptyState>
          )
        ) : (
          <>
            <p className="px-1 pb-1 text-xs text-slate-500 dark:text-slate-400">
              {documents.items.length}/{documents.total} tài liệu
            </p>
            <ul className="space-y-1" aria-label="Danh sách tài liệu">
              {documents.items.map((document) => (
                <DocumentRow
                  key={document.id}
                  document={document}
                  active={document.id === activeDocumentId}
                  onOpen={() => onOpen(document)}
                  onDelete={() => removal.ask(document)}
                  onReindex={() => reindex(document)}
                />
              ))}
            </ul>
            {documents.items.length < documents.total && (
              <button type="button" className={`${secondaryButton} mt-2 w-full`} onClick={documents.loadMore} disabled={documents.loadingMore}>
                {documents.loadingMore && <Spinner />}
                Tải thêm
              </button>
            )}
          </>
        )}
      </div>
      <ConfirmDialog
        open={removal.target !== null}
        title="Xóa tài liệu?"
        message={
          <>
            Tài liệu <strong className="break-all">{removal.target?.filename}</strong> và dữ liệu chỉ mục của nó sẽ bị xóa vĩnh viễn.
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
