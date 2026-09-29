import { useEffect, useState } from 'react';
import { getLlmHealth } from '../api/client';

type State = { kind: 'checking' } | { kind: 'ready'; model: string } | { kind: 'missing'; model: string } | { kind: 'down'; detail: string };

const CHECK_INTERVAL_MS = 60_000;

const STYLES: Record<State['kind'], { dot: string; label: string }> = {
  checking: { dot: 'bg-slate-400', label: 'Đang kiểm tra LLM…' },
  ready: { dot: 'bg-emerald-500', label: 'LLM sẵn sàng' },
  missing: { dot: 'bg-amber-500', label: 'Chưa có model LLM' },
  down: { dot: 'bg-red-500', label: 'LLM chưa chạy' },
};

/** Small indicator of GET /health/llm, rechecked periodically. */
export function LlmStatus() {
  const [state, setState] = useState<State>({ kind: 'checking' });

  useEffect(() => {
    let cancelled = false;
    const check = () =>
      getLlmHealth()
        .then((health) => !cancelled && setState({ kind: health.model_available ? 'ready' : 'missing', model: health.model }))
        .catch((caught: unknown) => !cancelled && setState({ kind: 'down', detail: caught instanceof Error ? caught.message : String(caught) }));
    void check();
    const timer = setInterval(check, CHECK_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const { dot, label } = STYLES[state.kind];
  const title = state.kind === 'down' ? state.detail : state.kind === 'checking' ? label : `Model: ${state.model}`;
  return (
    <span role="status" title={title} className="hidden items-center gap-1.5 text-xs text-slate-600 sm:inline-flex dark:text-slate-300">
      <span className={`size-2 rounded-full ${dot}`} aria-hidden="true" />
      {label}
    </span>
  );
}
