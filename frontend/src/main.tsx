import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { LoginPage } from './components/LoginPage';
import { EmptyState, ErrorBanner, Spinner } from './components/ui';
import { useSession } from './hooks/useSession';
import './index.css';

/** Shows the login page until there is a session; a 401 anywhere returns here. */
function Root() {
  const { state, login, logout, retry } = useSession();
  switch (state.status) {
    case 'loading':
      return (
        <div className="flex h-full items-center justify-center" role="status" aria-label="Đang tải">
          <Spinner className="size-6" />
        </div>
      );
    case 'error':
      return (
        <div className="mx-auto max-w-md p-6">
          <EmptyState icon="alert" title="Không kết nối được máy chủ" />
          <ErrorBanner message={state.message} onRetry={retry} />
        </div>
      );
    case 'signed-out':
      return <LoginPage expired={state.expired} onLogin={login} />;
    case 'signed-in':
      // Keyed by user: nothing of the previous account's state survives a re-login.
      return <App key={state.user.id} user={state.user} onLogout={logout} />;
  }
}

const root = document.getElementById('root');
if (!root) throw new Error('Missing #root element');

createRoot(root).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
