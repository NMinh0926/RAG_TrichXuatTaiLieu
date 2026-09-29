import { useRef, useState, type DragEvent } from 'react';
import type { JobWarning } from '../api/types';
import type { UploadItem, Uploads } from '../hooks/useUploads';
import { ACCEPTED_EXTENSIONS, STEP_LABELS } from '../lib/format';
import { Icon } from './Icon';
import { iconButton, ProgressBar, Spinner } from './ui';

function warningText({ page, step, error }: JobWarning): string {
  const where = page ? `Trang ${page}` : 'Tài liệu';
  return `${where}: ${step === 'ocr' ? 'không nhận dạng được chữ (OCR)' : `lỗi bước ${step}`} (${error})`;
}

function UploadRow({ item, onDismiss }: { item: UploadItem; onDismiss: () => void }) {
  const active = item.phase === 'uploading' || item.phase === 'processing';
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-2.5 text-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        <Icon name="file" className="size-4 shrink-0 text-slate-400" />
        <span className="min-w-0 flex-1 truncate font-medium" title={item.filename}>
          {item.filename}
        </span>
        {!active && (
          <button type="button" className={`${iconButton} size-7`} onClick={onDismiss} aria-label={`Ẩn ${item.filename}`}>
            <Icon name="close" className="size-4" />
          </button>
        )}
      </div>
      <div className="mt-1.5" aria-live="polite">
        {item.phase === 'uploading' && (
          <p className="flex items-center gap-2 text-slate-500 dark:text-slate-400">
            <Spinner className="size-3" /> Đang tải lên…
          </p>
        )}
        {item.phase === 'processing' && item.job && (
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-slate-500 dark:text-slate-400">
              <span>{STEP_LABELS[item.job.step]}</span>
              <span>{item.job.progress}%</span>
            </div>
            <ProgressBar value={item.job.progress} label={`Tiến trình xử lý ${item.filename}`} />
          </div>
        )}
        {item.phase === 'succeeded' && <p className="text-emerald-700 dark:text-emerald-400">Hoàn tất, đã sẵn sàng để hỏi đáp.</p>}
        {item.phase === 'duplicate' && <p className="text-sky-700 dark:text-sky-400">File này đã có sẵn trong thư viện.</p>}
        {item.phase === 'failed' && <p className="text-red-700 dark:text-red-400">{item.error}</p>}
        {item.phase === 'untracked' && <p className="text-amber-700 dark:text-amber-400">{item.error} Tài liệu có thể vẫn đang được xử lý.</p>}
      </div>
      {item.warnings.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-xs text-amber-700 dark:text-amber-400">
          {item.warnings.map((warning, i) => (
            <li key={i} className="flex gap-1">
              <Icon name="alert" className="mt-px size-3.5 shrink-0" />
              {warningText(warning)}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function UploadPanel({ uploads }: { uploads: Uploads }) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const hasFinished = uploads.items.some((item) => item.phase !== 'uploading' && item.phase !== 'processing');

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length) uploads.addFiles(Array.from(event.dataTransfer.files));
  };

  return (
    <section aria-label="Tải tài liệu lên" className="space-y-2">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex flex-col items-center gap-1.5 rounded-xl border-2 border-dashed px-3 py-4 text-center text-sm transition ${
          dragging
            ? 'border-brand-500 bg-brand-50 dark:bg-brand-700/20'
            : 'border-slate-300 dark:border-slate-700'
        }`}
      >
        <Icon name="upload" className="size-6 text-slate-400" />
        <p className="text-slate-600 dark:text-slate-300">
          Kéo thả file vào đây hoặc{' '}
          <button type="button" onClick={() => input.current?.click()} className="font-medium text-brand-600 underline dark:text-brand-500">
            chọn file
          </button>
        </p>
        <p className="text-xs text-slate-500 dark:text-slate-400">PDF, DOCX, XLS/XLSX, PNG, JPG — chọn được nhiều file</p>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPTED_EXTENSIONS.join(',')}
          className="sr-only"
          tabIndex={-1}
          aria-label="Chọn file để tải lên"
          onChange={(event) => {
            if (event.target.files?.length) uploads.addFiles(Array.from(event.target.files));
            event.target.value = '';
          }}
        />
      </div>
      {uploads.items.length > 0 && (
        <>
          <ul className="max-h-64 space-y-2 overflow-y-auto" aria-label="Tiến trình tải lên">
            {uploads.items.map((item) => (
              <UploadRow key={item.key} item={item} onDismiss={() => uploads.dismiss(item.key)} />
            ))}
          </ul>
          {hasFinished && (
            <button type="button" onClick={uploads.clearFinished} className="text-xs text-slate-500 underline dark:text-slate-400">
              Ẩn các mục đã xong
            </button>
          )}
        </>
      )}
    </section>
  );
}
