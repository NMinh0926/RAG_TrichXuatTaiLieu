import { useEffect, useId, useRef, type ReactNode } from 'react';
import { Icon } from './Icon';
import { iconButton } from './ui';

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}

/** Modal built on <dialog> (focus trap, Escape closes). */
export function Modal({ open, title, onClose, children, wide = false }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className={`m-auto max-h-[calc(100vh-2rem)] ${wide ? 'w-[min(40rem,calc(100vw-2rem))]' : 'w-[min(26rem,calc(100vw-2rem))]'} rounded-xl bg-white p-5 text-slate-900 shadow-xl backdrop:bg-slate-950/50 dark:bg-slate-900 dark:text-slate-100`}
    >
      <div className="mb-3 flex items-center gap-2">
        <h2 id={titleId} className="flex-1 text-lg font-semibold">
          {title}
        </h2>
        <button type="button" className={iconButton} onClick={onClose} aria-label="Đóng">
          <Icon name="close" />
        </button>
      </div>
      {open && children}
    </dialog>
  );
}
