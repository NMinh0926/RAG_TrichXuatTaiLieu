import { useEffect, useRef, useState } from 'react';
import type { User } from '../api/types';
import { Icon, type IconName } from './Icon';
import { PasswordDialog } from './PasswordDialog';
import { SystemStatusDialog } from './SystemStatusDialog';
import { UsersDialog } from './UsersDialog';
import { iconButton } from './ui';

type Dialog = 'status' | 'users' | 'password' | null;

export function UserMenu({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !container.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', close);
    };
  }, [open]);

  const item = (icon: IconName, label: string, action: () => void) => (
    <button
      type="button"
      role="menuitem"
      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
      onClick={() => {
        setOpen(false);
        action();
      }}
    >
      <Icon name={icon} className="size-4" />
      {label}
    </button>
  );

  return (
    <div ref={container} className="relative">
      <button
        type="button"
        className={`${iconButton} w-auto gap-1.5 px-2`}
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={user.username}
      >
        <Icon name="user" />
        <span className="hidden max-w-32 truncate text-sm sm:inline">{user.username}</span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-1 w-56 rounded-lg border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-800 dark:bg-slate-900"
        >
          <p className="px-2 py-1 text-xs text-slate-500 dark:text-slate-400">
            {user.username} · {user.role === 'admin' ? 'Quản trị viên' : 'Người dùng'}
          </p>
          {item('activity', 'Trạng thái hệ thống', () => setDialog('status'))}
          {user.role === 'admin' && item('users', 'Quản lý người dùng', () => setDialog('users'))}
          {item('key', 'Đổi mật khẩu', () => setDialog('password'))}
          {item('logout', 'Đăng xuất', onLogout)}
        </div>
      )}
      <SystemStatusDialog open={dialog === 'status'} onClose={() => setDialog(null)} />
      {user.role === 'admin' && <UsersDialog open={dialog === 'users'} onClose={() => setDialog(null)} currentUserId={user.id} />}
      <PasswordDialog open={dialog === 'password'} onClose={() => setDialog(null)} />
    </div>
  );
}
