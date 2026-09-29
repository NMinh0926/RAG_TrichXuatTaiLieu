import { useEffect, useId, useRef, useState } from 'react';
import { listDocuments } from '../api/client';
import type { DocumentInfo } from '../api/types';
import { Icon } from './Icon';
import { ErrorBanner, iconButton, ListSkeleton } from './ui';

export interface ScopedDocument {
  id: string;
  filename: string;
}

interface ScopeSelectorProps {
  selectAll: boolean;
  selected: ScopedDocument[];
  disabled: boolean;
  onChange: (selectAll: boolean, selected: ScopedDocument[]) => void;
}

// Ready documents offered in the picker (the API's maximum page size).
const PICKER_LIMIT = 100;

function DocumentPicker({ selected, onToggle }: { selected: ScopedDocument[]; onToggle: (document: DocumentInfo) => void }) {
  const [documents, setDocuments] = useState<DocumentInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    let cancelled = false;
    listDocuments(1, PICKER_LIMIT)
      .then((page) => !cancelled && setDocuments(page.items.filter((document) => document.status === 'ready')))
      .catch((caught: unknown) => !cancelled && setError(caught instanceof Error ? caught.message : String(caught)));
    return () => {
      cancelled = true;
    };
  }, []);

  const needle = filter.trim().toLowerCase();
  const visible = documents?.filter((document) => document.filename.toLowerCase().includes(needle)) ?? [];

  return (
    <div
      role="dialog"
      aria-label="Chọn tài liệu để hỏi"
      className="absolute bottom-full left-0 z-20 mb-2 w-[min(24rem,calc(100vw-2rem))] rounded-xl border border-slate-200 bg-white p-3 shadow-xl dark:border-slate-700 dark:bg-slate-900"
    >
      <input
        type="search"
        autoFocus
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        placeholder="Lọc theo tên…"
        aria-label="Lọc tài liệu"
        className="mb-2 w-full rounded-lg border border-slate-300 bg-transparent px-3 py-1.5 text-sm dark:border-slate-700"
      />
      {error && <ErrorBanner message={error} />}
      {!documents && !error && <ListSkeleton rows={3} label="Đang tải tài liệu" />}
      {documents && visible.length === 0 && <p className="py-3 text-center text-sm text-slate-500">Không có tài liệu sẵn sàng.</p>}
      <ul className="max-h-60 space-y-0.5 overflow-y-auto">
        {visible.map((document) => (
          <li key={document.id}>
            <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-slate-100 dark:hover:bg-slate-800">
              <input
                type="checkbox"
                className="accent-brand-600"
                checked={selected.some((item) => item.id === document.id)}
                onChange={() => onToggle(document)}
              />
              <span className="truncate">{document.filename}</span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** "All documents" or a multi-selection of ready documents, sent as `document_ids`. */
export function ScopeSelector({ selectAll, selected, disabled, onChange }: ScopeSelectorProps) {
  const [picking, setPicking] = useState(false);
  const groupName = useId();
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!picking) return;
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setPicking(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setPicking(false);
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [picking]);

  const toggle = (document: DocumentInfo) => {
    const exists = selected.some((item) => item.id === document.id);
    onChange(false, exists ? selected.filter((item) => item.id !== document.id) : [...selected, { id: document.id, filename: document.filename }]);
  };

  return (
    <div ref={root} className="relative flex flex-wrap items-center gap-2 text-sm">
      <fieldset className="flex items-center gap-1 rounded-lg bg-slate-100 p-0.5 dark:bg-slate-800" disabled={disabled}>
        <legend className="sr-only">Phạm vi tìm kiếm</legend>
        {[
          { value: true, label: 'Tất cả tài liệu' },
          { value: false, label: 'Tài liệu đã chọn' },
        ].map(({ value, label }) => (
          <label
            key={label}
            className={`cursor-pointer rounded-md px-2.5 py-1 transition has-focus-visible:outline-2 has-focus-visible:outline-brand-500 ${
              selectAll === value ? 'bg-white font-medium shadow-sm dark:bg-slate-700' : 'text-slate-600 dark:text-slate-300'
            }`}
          >
            <input
              type="radio"
              name={groupName}
              className="sr-only"
              checked={selectAll === value}
              onChange={() => {
                onChange(value, selected);
                if (!value && selected.length === 0) setPicking(true);
              }}
            />
            {label}
          </label>
        ))}
      </fieldset>
      {!selectAll && (
        <>
          {selected.map((document) => (
            <span key={document.id} className="inline-flex max-w-48 items-center gap-1 rounded-full bg-brand-50 py-0.5 pr-1 pl-2.5 text-xs text-brand-700 dark:bg-brand-700/20 dark:text-brand-100">
              <span className="truncate" title={document.filename}>
                {document.filename}
              </span>
              <button
                type="button"
                disabled={disabled}
                className={`${iconButton} size-5 rounded-full`}
                onClick={() => onChange(false, selected.filter((item) => item.id !== document.id))}
                aria-label={`Bỏ chọn ${document.filename}`}
              >
                <Icon name="close" className="size-3" />
              </button>
            </span>
          ))}
          <button
            type="button"
            disabled={disabled}
            onClick={() => setPicking((open) => !open)}
            aria-expanded={picking}
            className="rounded-md px-2 py-1 text-xs font-medium text-brand-600 hover:bg-brand-50 disabled:opacity-50 dark:text-brand-500 dark:hover:bg-brand-700/20"
          >
            {selected.length ? 'Thay đổi…' : 'Chọn tài liệu…'}
          </button>
          {picking && <DocumentPicker selected={selected} onToggle={toggle} />}
        </>
      )}
    </div>
  );
}
