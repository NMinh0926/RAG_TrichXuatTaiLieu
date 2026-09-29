import { lazy, Suspense, useEffect, useState } from 'react';
import { documentFileUrl, getDocument } from '../api/client';
import type { Bbox, DocumentInfo } from '../api/types';
import { fileKind, formatBytes, formatDate, STATUS_LABELS } from '../lib/format';
import { Icon } from './Icon';
import { ErrorBanner, iconButton, Skeleton, StatusBadge } from './ui';

// pdf.js is large: load it only when a PDF is opened.
const PdfViewer = lazy(() => import('./PdfViewer'));

export interface ViewerTarget {
  documentId: string;
  filename: string;
  page: number | null;
  sheet: string | null;
  bboxes: Bbox[];
  /** Changes on every open request, so re-opening the same source scrolls to it again. */
  nonce: number;
}

function Metadata({ document, sheet }: { document: DocumentInfo; sheet: string | null }) {
  const rows: [string, string][] = [
    ['Loại file', document.content_type],
    ['Dung lượng', formatBytes(document.size_bytes)],
    ['Số trang', document.page_count?.toString() ?? '—'],
    ['Số đoạn đã lập chỉ mục', document.chunk_count.toString()],
    ['Trạng thái', STATUS_LABELS[document.status]],
    ['Ngày tải lên', formatDate(document.created_at)],
  ];
  if (sheet) rows.unshift(['Sheet được trích dẫn', sheet]);
  return (
    <div className="space-y-4 p-4">
      <p className="text-sm text-slate-600 dark:text-slate-300">Không thể xem trước loại file này trong trình duyệt. Bạn có thể tải file về để mở.</p>
      <dl className="divide-y divide-slate-200 rounded-lg border border-slate-200 text-sm dark:divide-slate-800 dark:border-slate-800">
        {rows.map(([label, value]) => (
          <div key={label} className="flex gap-3 px-3 py-2">
            <dt className="w-40 shrink-0 text-slate-500 dark:text-slate-400">{label}</dt>
            <dd className="min-w-0 break-words">{value}</dd>
          </div>
        ))}
      </dl>
      {document.error_message && <ErrorBanner message={document.error_message} />}
      <a
        href={documentFileUrl(document.id)}
        download={document.filename}
        className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700"
      >
        <Icon name="download" className="size-4" />
        Tải file xuống
      </a>
    </div>
  );
}

const pdfFallback = (
  <div role="status" aria-label="Đang tải trình xem PDF" className="p-4">
    <Skeleton className="aspect-[1/1.3] w-full" />
  </div>
);

/** Right-hand panel (full-screen overlay on small screens) showing the opened document. */
export function ViewerPanel({ target, onClose }: { target: ViewerTarget; onClose: () => void }) {
  const [document, setDocument] = useState<DocumentInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { documentId } = target;

  useEffect(() => {
    let cancelled = false;
    setDocument(null);
    setError(null);
    getDocument(documentId)
      .then((info) => !cancelled && setDocument(info))
      .catch((caught: unknown) => !cancelled && setError(caught instanceof Error ? caught.message : String(caught)));
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // Escape inside a dialog or popover closes that one, not the viewer.
      const inDialog = event.target instanceof Element && event.target.closest('dialog, [role="dialog"]');
      if (event.key === 'Escape' && !inDialog) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const kind = document ? fileKind(document.filename, document.content_type) : null;
  const fileUrl = documentFileUrl(documentId);

  return (
    <aside
      aria-label={`Xem tài liệu ${target.filename}`}
      className="fixed inset-0 z-50 flex flex-col bg-white lg:static lg:z-auto lg:w-[45%] lg:max-w-4xl lg:min-w-96 lg:border-l lg:border-slate-200 dark:bg-slate-900 lg:dark:border-slate-800"
    >
      <header className="flex items-center gap-2 border-b border-slate-200 px-3 py-2 dark:border-slate-800">
        <Icon name="file" className="size-5 shrink-0 text-slate-400" />
        <h2 className="min-w-0 flex-1 truncate font-medium" title={target.filename}>
          {target.filename}
        </h2>
        {document && <StatusBadge status={document.status} />}
        <a href={fileUrl} target="_blank" rel="noopener noreferrer" className={iconButton} aria-label="Mở file gốc trong thẻ mới" title="Mở file gốc">
          <Icon name="download" />
        </a>
        <button type="button" className={iconButton} onClick={onClose} aria-label="Đóng trình xem">
          <Icon name="close" />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-hidden">
        {error ? (
          <div className="p-4">
            <ErrorBanner message={error} />
          </div>
        ) : !document ? (
          pdfFallback
        ) : kind === 'pdf' ? (
          <Suspense fallback={pdfFallback}>
            <PdfViewer url={fileUrl} target={target} />
          </Suspense>
        ) : kind === 'image' ? (
          <div className="h-full overflow-auto bg-slate-100 p-4 dark:bg-slate-800">
            <img src={fileUrl} alt={document.filename} className="mx-auto max-w-full rounded shadow" />
          </div>
        ) : (
          <div className="h-full overflow-auto">
            <Metadata document={document} sheet={target.sheet} />
          </div>
        )}
      </div>
    </aside>
  );
}
