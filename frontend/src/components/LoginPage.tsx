import { useId, useState, type FormEvent } from 'react';
import { Icon } from './Icon';
import { ErrorBanner, primaryButton, Spinner } from './ui';

interface LoginPageProps {
  expired: boolean;
  onLogin: (username: string, password: string) => Promise<void>;
}

export const inputClass =
  'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900';

export function LoginPage({ expired, onLogin }: LoginPageProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const userId = useId();
  const passwordId = useId();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    onLogin(username.trim(), password)
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught)))
      .finally(() => setBusy(false));
  };

  return (
    <main className="flex h-full items-center justify-center p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900"
      >
        <div className="flex items-center gap-2">
          <span className="flex size-9 items-center justify-center rounded-lg bg-brand-600 text-white" aria-hidden="true">
            <Icon name="file" className="size-5" />
          </span>
          <h1 className="text-lg font-semibold">Trợ lý tài liệu</h1>
        </div>
        <p className="text-sm text-slate-600 dark:text-slate-300">
          {expired ? 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.' : 'Đăng nhập bằng tài khoản do quản trị viên cấp.'}
        </p>
        {error && <ErrorBanner message={error} />}
        <div className="space-y-1">
          <label htmlFor={userId} className="text-sm font-medium">
            Tên đăng nhập
          </label>
          <input
            id={userId}
            className={inputClass}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoFocus
            required
            maxLength={64}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor={passwordId} className="text-sm font-medium">
            Mật khẩu
          </label>
          <input
            id={passwordId}
            type="password"
            className={inputClass}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
            maxLength={256}
          />
        </div>
        <button type="submit" className={`${primaryButton} w-full`} disabled={busy || !username.trim() || !password}>
          {busy && <Spinner />}
          Đăng nhập
        </button>
      </form>
    </main>
  );
}
