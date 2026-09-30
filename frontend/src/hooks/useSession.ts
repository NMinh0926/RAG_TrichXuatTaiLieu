import { useCallback, useEffect, useState } from 'react';
import { currentUser, login as loginRequest, logout as logoutRequest, onUnauthorized } from '../api/client';
import type { User } from '../api/types';

export type SessionState =
  | { status: 'loading' }
  | { status: 'signed-out'; expired: boolean }
  | { status: 'signed-in'; user: User }
  | { status: 'error'; message: string };

/** The signed-in user from the HttpOnly session cookie; any 401 from the API signs the page out. */
export function useSession() {
  const [state, setState] = useState<SessionState>({ status: 'loading' });

  const check = useCallback(() => {
    setState({ status: 'loading' });
    currentUser()
      .then((user) => setState(user ? { status: 'signed-in', user } : { status: 'signed-out', expired: false }))
      .catch((caught: unknown) => setState({ status: 'error', message: caught instanceof Error ? caught.message : String(caught) }));
  }, []);

  useEffect(check, [check]);

  useEffect(
    () =>
      onUnauthorized(() =>
        setState((current) => (current.status === 'signed-in' ? { status: 'signed-out', expired: true } : current)),
      ),
    [],
  );

  const login = useCallback(async (username: string, password: string) => {
    const session = await loginRequest(username, password);
    setState({ status: 'signed-in', user: session.user });
  }, []);

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } finally {
      setState({ status: 'signed-out', expired: false });
    }
  }, []);

  return { state, login, logout, retry: check };
}
