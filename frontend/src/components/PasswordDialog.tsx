import { useState, type FormEvent } from 'react';
import { changePassword } from '../api/client';
import { inputClass } from './LoginPage';
import { Modal } from './Modal';
import { PasswordInput } from './PasswordInput';
import { ErrorBanner, primaryButton, Spinner } from './ui';

const MIN_PASSWORD = 8;

export function PasswordDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const close = () => {
    setCurrent('');
    setNext('');
    setRepeat('');
    setError(null);
    setDone(false);
    onClose();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (next !== repeat) {
      setError('Mật khẩu mới nhập lại không khớp.');
      return;
    }
    setBusy(true);
    setError(null);
    changePassword(current, next)
      .then(() => setDone(true))
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught)))
      .finally(() => setBusy(false));
  };

  return (
    <Modal open={open} title="Đổi mật khẩu" onClose={close}>
      {done ? (
        <p className="text-sm text-emerald-700 dark:text-emerald-400">
          Đã đổi mật khẩu. Các phiên đăng nhập khác của bạn đã bị đăng xuất.
        </p>
      ) : (
        <form onSubmit={submit} className="space-y-3">
          {error && <ErrorBanner message={error} />}
          <PasswordInput
            className={inputClass}
            placeholder="Mật khẩu hiện tại"
            aria-label="Mật khẩu hiện tại"
            autoComplete="current-password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            required
          />
          <PasswordInput
            className={inputClass}
            placeholder={`Mật khẩu mới (ít nhất ${MIN_PASSWORD} ký tự)`}
            aria-label="Mật khẩu mới"
            autoComplete="new-password"
            minLength={MIN_PASSWORD}
            maxLength={256}
            value={next}
            onChange={(event) => setNext(event.target.value)}
            required
          />
          <PasswordInput
            className={inputClass}
            placeholder="Nhập lại mật khẩu mới"
            aria-label="Nhập lại mật khẩu mới"
            autoComplete="new-password"
            value={repeat}
            onChange={(event) => setRepeat(event.target.value)}
            required
          />
          <button type="submit" className={`${primaryButton} w-full`} disabled={busy || next.length < MIN_PASSWORD}>
            {busy && <Spinner />}
            Đổi mật khẩu
          </button>
        </form>
      )}
    </Modal>
  );
}
