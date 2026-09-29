import { useCallback, useState } from 'react';

export interface ConfirmAction<T> {
  target: T | null;
  busy: boolean;
  error: string | null;
  ask: (target: T) => void;
  cancel: () => void;
  confirm: () => void;
}

/** State of a confirmation dialog for an async action (e.g. delete); errors stay in the dialog. */
export function useConfirmAction<T>(action: (target: T) => Promise<void>, onDone: (target: T) => void): ConfirmAction<T> {
  const [target, setTarget] = useState<T | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = useCallback((next: T) => {
    setTarget(next);
    setError(null);
  }, []);

  const cancel = useCallback(() => setTarget(null), []);

  const confirm = useCallback(() => {
    if (target === null) return;
    setBusy(true);
    setError(null);
    action(target)
      .then(() => {
        setTarget(null);
        onDone(target);
      })
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught)))
      .finally(() => setBusy(false));
  }, [action, onDone, target]);

  return { target, busy, error, ask, cancel, confirm };
}
