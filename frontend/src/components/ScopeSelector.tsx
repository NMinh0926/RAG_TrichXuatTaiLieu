import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { listDocuments } from '../api/client';
import type { DocumentInfo } from '../api/types';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { usePagedList } from '../hooks/usePagedList';
import { MAX_SCOPE_DOCUMENTS, scopeFull, toggleScoped, type ScopedDocument } from '../lib/scope';
import { Icon } from './Icon';
import { ErrorBanner, iconButton, ListSkeleton, secondaryButton, Spinner } from './ui';

interface ScopeSelectorProps {
  selectAll: boolean;
  selected: ScopedDocument[];
  disabled: boolean;
  onChange: (selectAll: boolean, selected: ScopedDocument[]) => void;
}

// The API's maximum page size; the name filter is applied by the server so older documents stay reachable.
const PICKER_PAGE_SIZE = 100;

function DocumentPicker({ selected, onToggle }: { selected: ScopedDocument[]; onToggle: (document: DocumentInfo) => void }) {
  const [filter, setFilter] = useState('');
  const q = useDebouncedValue(filter.trim(), 300);
  const fetchPage = useCallback((page: number, pageSize: number) => listDocuments(page, pageSize, q), [q]);
  const documents = usePagedList(fetchPage, PICKER_PAGE_SIZE);
  const visible = documents.items.filter((document) => document.status === 'ready');
  const full = scopeFull(selected);

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
        placeholder="Tìm theo tên file…"
        aria-label="Tìm tài liệu để chọn"
        className="mb-2 w-full rounded-lg border border-slate-300 bg-transparent px-3 py-1.5 text-sm dark:border-slate-700"
      />
      {documents.error && <ErrorBanner message={documents.error} onRetry={documents.refresh} />}
      <p className={`mb-1 px-2 text-xs ${full ? 'text-amber-700 dark:text-amber-400' : 'text-slate-500 dark:text-slate-400'}`} aria-live="polite">
        Đã chọn {selected.length}/{MAX_SCOPE_DOCUMENTS}
        {full && ` · Tối đa ${MAX_SCOPE_DOCUMENTS} tài liệu cho một câu hỏi`}
      </p>
      {documents.loading ? (
        <ListSkeleton rows={3} label="Đang tải tài liệu" />
      ) : (
        <div className="max-h-60 overflow-y-auto">
          {visible.length === 0 && !documents.error && (
            <p className="py-3 text-center text-sm text-slate-500">{q ? 'Không tìm thấy tài liệu sẵn sàng phù hợp.' : 'Không có tài liệu sẵn sàng.'}</p>
          )}
          <ul className="space-y-0.5">
            {visible.map((document) => {
              const checked = selected.some((item) => item.id === document.id);
              return (
                <li key={document.id}>
                  <label
                    className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-sm ${
                      checked || !full ? 'cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-800' : 'cursor-not-allowed opacity-50'
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="accent-brand-600"
                      checked={checked}
                      disabled={!checked && full}
                      onChange={() => onToggle(document)}
                    />
                    <span className="min-w-0 truncate" title={document.filename}>
                      {document.filename}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          {documents.items.length < documents.total && (
            <button type="button" className={`${secondaryButton} mt-1 w-full`} onClick={documents.loadMore} disabled={documents.loadingMore}>
              {documents.loadingMore && <Spinner />}
              Tải thêm
            </button>
          )}
        </div>
      )}
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
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Handled here: other Escape listeners (the document viewer) must not act on it too.
      event.preventDefault();
      setPicking(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [picking]);

  const toggle = (document: DocumentInfo) => onChange(false, toggleScoped(selected, document));

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
