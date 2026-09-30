import { useCallback, useEffect, useState } from 'react';
import { getSystemStatus } from '../api/client';
import type { SystemStatus } from '../api/types';
import { STATUS_LABELS } from '../lib/format';
import { CHECK_LABELS, describeCheck, orderedChecks } from '../lib/status';
import { Modal } from './Modal';
import { ErrorBanner, ListSkeleton, secondaryButton, Spinner } from './ui';

const CONFIG_LABELS: Record<string, string> = {
  llm_model: 'LLM',
  llm_summary_model: 'LLM tóm tắt (map)',
  embedding_model: 'Embedding',
  reranker_model: 'Reranker',
  top_k: 'Số đoạn ngữ cảnh (TOP_K)',
  chunk_size: 'Kích thước chunk (token)',
  ocr_lang: 'Ngôn ngữ OCR',
  max_upload_mb: 'Giới hạn upload (MB)',
  max_pdf_pages: 'Giới hạn số trang PDF',
};

export function SystemStatusDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getSystemStatus()
      .then(setStatus)
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  return (
    <Modal open={open} title="Trạng thái hệ thống" onClose={onClose} wide>
      {error && <ErrorBanner message={error} onRetry={load} />}
      {!status && loading && <ListSkeleton label="Đang kiểm tra hệ thống" />}
      {status && (
        <div className="space-y-4 text-sm">
          <p className={status.status === 'ok' ? 'text-emerald-700 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-400'}>
            {status.status === 'ok' ? 'Mọi thành phần đang hoạt động.' : 'Một số thành phần đang gặp sự cố.'}
          </p>
          <ul className="divide-y divide-slate-200 dark:divide-slate-800" aria-label="Thành phần">
            {orderedChecks(status.checks).map(([name, check]) => (
              <li key={name} className="flex items-center gap-2 py-1.5">
                <span aria-hidden="true" className={`size-2.5 rounded-full ${check.ok ? 'bg-emerald-500' : 'bg-red-500'}`} />
                <span className="flex-1">{CHECK_LABELS[name] ?? name}</span>
                <span className={check.ok ? 'text-slate-500 dark:text-slate-400' : 'text-red-700 dark:text-red-400'}>
                  {describeCheck(check)}
                </span>
              </li>
            ))}
          </ul>
          <div className="grid gap-4 sm:grid-cols-2">
            <section>
              <h3 className="mb-1 font-medium">Tài liệu và hàng đợi</h3>
              <dl className="space-y-0.5 text-slate-600 dark:text-slate-300">
                {Object.entries(status.documents_by_status ?? {}).map(([name, count]) => (
                  <div key={name} className="flex justify-between">
                    <dt>{STATUS_LABELS[name as keyof typeof STATUS_LABELS] ?? name}</dt>
                    <dd>{count}</dd>
                  </div>
                ))}
                {status.queue && (
                  <>
                    <div className="flex justify-between">
                      <dt>Worker đang chạy</dt>
                      <dd>{status.queue.workers}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt>Tác vụ đang chờ</dt>
                      <dd>{status.queue.queued_jobs}</dd>
                    </div>
                  </>
                )}
              </dl>
            </section>
            <section>
              <h3 className="mb-1 font-medium">Cấu hình</h3>
              <dl className="space-y-0.5 text-slate-600 dark:text-slate-300">
                {Object.entries(status.config).map(([name, value]) => (
                  <div key={name} className="flex justify-between gap-2">
                    <dt>{CONFIG_LABELS[name] ?? name}</dt>
                    <dd className="truncate text-right" title={String(value ?? '')}>
                      {value ?? '—'}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          </div>
          <button type="button" className={secondaryButton} onClick={load} disabled={loading}>
            {loading && <Spinner />}
            Kiểm tra lại
          </button>
        </div>
      )}
    </Modal>
  );
}
