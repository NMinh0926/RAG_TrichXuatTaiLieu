import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { createUser, listUsers, updateUser } from '../api/client';
import type { Role, User } from '../api/types';
import { inputClass } from './LoginPage';
import { Modal } from './Modal';
import { ErrorBanner, ListSkeleton, primaryButton, secondaryButton, Spinner } from './ui';

const MIN_PASSWORD = 8;

function message(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught);
}

/** Admin only: create accounts, reset passwords, change roles, lock/unlock. */
export function UsersDialog({ open, onClose, currentUserId }: { open: boolean; onClose: () => void; currentUserId: string }) {
  const [users, setUsers] = useState<User[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<Role>('user');
  const [busy, setBusy] = useState(false);
  const [resetTarget, setResetTarget] = useState<User | null>(null);
  const [resetPassword, setResetPassword] = useState('');

  const load = useCallback(() => {
    setError(null);
    listUsers()
      .then(setUsers)
      .catch((caught: unknown) => setError(message(caught)));
  }, []);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const replace = (user: User) => setUsers((current) => current?.map((item) => (item.id === user.id ? user : item)) ?? null);

  const run = (action: Promise<User>, done: (user: User) => void) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    action
      .then(done)
      .catch((caught: unknown) => setError(message(caught)))
      .finally(() => setBusy(false));
  };

  const create = (event: FormEvent) => {
    event.preventDefault();
    run(createUser(username.trim(), password, role), (user) => {
      setUsers((current) => [...(current ?? []), user].sort((a, b) => a.username.localeCompare(b.username)));
      setNotice(`Đã tạo tài khoản ${user.username}.`);
      setUsername('');
      setPassword('');
      setRole('user');
    });
  };

  const submitReset = (event: FormEvent) => {
    event.preventDefault();
    if (!resetTarget) return;
    run(updateUser(resetTarget.id, { password: resetPassword }), (updated) => {
      replace(updated);
      setNotice(`Đã đặt lại mật khẩu cho ${updated.username}; các phiên cũ bị đăng xuất.`);
      setResetTarget(null);
      setResetPassword('');
    });
  };

  return (
    <Modal open={open} title="Quản lý người dùng" onClose={onClose} wide>
      <div className="space-y-4 text-sm">
        {error && <ErrorBanner message={error} />}
        {notice && <p className="text-emerald-700 dark:text-emerald-400">{notice}</p>}
        {!users ? (
          !error && <ListSkeleton label="Đang tải danh sách người dùng" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead className="text-xs text-slate-500 dark:text-slate-400">
                <tr>
                  <th className="py-1 font-medium">Tài khoản</th>
                  <th className="py-1 font-medium">Vai trò</th>
                  <th className="py-1 font-medium">Trạng thái</th>
                  <th className="py-1" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                {users.map((user) => {
                  const self = user.id === currentUserId;
                  return (
                    <tr key={user.id}>
                      <td className="py-1.5 font-medium">{user.username}</td>
                      <td className="py-1.5">
                        <select
                          aria-label={`Vai trò của ${user.username}`}
                          className="rounded border border-slate-300 bg-transparent px-1 py-0.5 dark:border-slate-700"
                          value={user.role}
                          disabled={busy || self}
                          onChange={(event) => run(updateUser(user.id, { role: event.target.value as Role }), replace)}
                        >
                          <option value="user">Người dùng</option>
                          <option value="admin">Quản trị</option>
                        </select>
                      </td>
                      <td className="py-1.5">{user.is_active ? 'Hoạt động' : 'Đã khóa'}</td>
                      <td className="flex justify-end gap-1 py-1.5">
                        <button
                          type="button"
                          className={secondaryButton}
                          disabled={busy || self}
                          title={self ? 'Đổi mật khẩu của bạn trong menu tài khoản → Đổi mật khẩu' : undefined}
                          onClick={() => setResetTarget(user)}
                        >
                          Đặt lại mật khẩu
                        </button>
                        <button
                          type="button"
                          className={secondaryButton}
                          disabled={busy || self}
                          onClick={() => run(updateUser(user.id, { is_active: !user.is_active }), replace)}
                        >
                          {user.is_active ? 'Khóa' : 'Mở khóa'}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {resetTarget && (
          <form onSubmit={submitReset} className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-100 p-2 dark:bg-slate-800">
            <span className="w-full font-medium sm:w-auto">Mật khẩu mới cho {resetTarget.username}</span>
            <input
              type="password"
              className={`${inputClass} flex-1`}
              aria-label={`Mật khẩu mới cho ${resetTarget.username}`}
              autoComplete="new-password"
              minLength={MIN_PASSWORD}
              maxLength={256}
              value={resetPassword}
              onChange={(event) => setResetPassword(event.target.value)}
              required
            />
            <button type="submit" className={primaryButton} disabled={busy || resetPassword.length < MIN_PASSWORD}>
              Lưu
            </button>
            <button type="button" className={secondaryButton} onClick={() => setResetTarget(null)}>
              Hủy
            </button>
          </form>
        )}
        <form onSubmit={create} className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-800">
          <h3 className="font-medium">Tạo tài khoản</h3>
          <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <input
              className={inputClass}
              placeholder="Tên đăng nhập"
              aria-label="Tên đăng nhập mới"
              pattern="[A-Za-z0-9._\-]{3,64}"
              title="3–64 ký tự: chữ không dấu, số, dấu chấm, gạch dưới, gạch ngang"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
            <input
              type="password"
              className={inputClass}
              placeholder={`Mật khẩu (≥ ${MIN_PASSWORD} ký tự)`}
              aria-label="Mật khẩu cho tài khoản mới"
              autoComplete="new-password"
              minLength={MIN_PASSWORD}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <select
              aria-label="Vai trò của tài khoản mới"
              className={inputClass}
              value={role}
              onChange={(event) => setRole(event.target.value as Role)}
            >
              <option value="user">Người dùng</option>
              <option value="admin">Quản trị</option>
            </select>
          </div>
          <button type="submit" className={primaryButton} disabled={busy || password.length < MIN_PASSWORD}>
            {busy && <Spinner />}
            Tạo tài khoản
          </button>
        </form>
      </div>
    </Modal>
  );
}
