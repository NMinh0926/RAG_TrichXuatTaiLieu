import { useId, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Icon } from './Icon';
import { iconButton, primaryButton } from './ui';

type Tab = 'documents' | 'history';

const TABS: { id: Tab; label: string }[] = [
  { id: 'documents', label: 'Tài liệu' },
  { id: 'history', label: 'Lịch sử' },
];

interface SidebarProps {
  open: boolean;
  /** Docked column (desktop) rather than a drawer. */
  docked: boolean;
  onClose: () => void;
  onNewChat: () => void;
  documents: ReactNode;
  history: ReactNode;
}

/** Left column; an off-canvas drawer below the lg breakpoint. Both tab panels stay mounted (uploads keep running). */
export function Sidebar({ open, docked, onClose, onNewChat, documents, history }: SidebarProps) {
  const [tab, setTab] = useState<Tab>('documents');
  const baseId = useId();

  const onTabKey = (event: KeyboardEvent) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    const next = tab === 'documents' ? 'history' : 'documents';
    setTab(next);
    document.getElementById(`${baseId}-tab-${next}`)?.focus();
  };

  return (
    <>
      {open && !docked && <div className="fixed inset-0 z-30 bg-slate-950/40 lg:hidden" onClick={onClose} aria-hidden="true" />}
      <aside
        aria-label="Thanh bên"
        inert={!docked && !open}
        className={`fixed inset-y-0 left-0 z-40 flex w-[min(20rem,88vw)] flex-col border-r border-slate-200 bg-slate-100 transition-transform duration-200 lg:static lg:z-auto lg:w-80 lg:translate-x-0 dark:border-slate-800 dark:bg-slate-950 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex items-center gap-2 p-3">
          <button type="button" className={`${primaryButton} flex-1`} onClick={onNewChat}>
            <Icon name="plus" className="size-4" />
            Cuộc trò chuyện mới
          </button>
          <button type="button" className={`${iconButton} lg:hidden`} onClick={onClose} aria-label="Đóng thanh bên">
            <Icon name="close" />
          </button>
        </div>
        <div role="tablist" aria-label="Nội dung thanh bên" className="mx-3 grid grid-cols-2 rounded-lg bg-slate-200 p-1 dark:bg-slate-900">
          {TABS.map(({ id, label }) => (
            <button
              key={id}
              id={`${baseId}-tab-${id}`}
              type="button"
              role="tab"
              aria-selected={tab === id}
              aria-controls={`${baseId}-panel-${id}`}
              tabIndex={tab === id ? 0 : -1}
              onClick={() => setTab(id)}
              onKeyDown={onTabKey}
              className={`rounded-md py-1.5 text-sm font-medium transition ${
                tab === id ? 'bg-white shadow-sm dark:bg-slate-800' : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {TABS.map(({ id }) => (
          <div
            key={id}
            id={`${baseId}-panel-${id}`}
            role="tabpanel"
            aria-labelledby={`${baseId}-tab-${id}`}
            hidden={tab !== id}
            className="min-h-0 flex-1 p-3"
          >
            {id === 'documents' ? documents : history}
          </div>
        ))}
      </aside>
    </>
  );
}
