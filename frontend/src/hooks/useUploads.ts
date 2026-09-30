import { useCallback, useEffect, useRef, useState } from 'react';
import { subscribeJob, uploadDocument } from '../api/client';
import type { Job, JobWarning } from '../api/types';
import { isAcceptedFile } from '../lib/format';

export type UploadPhase = 'uploading' | 'processing' | 'succeeded' | 'failed' | 'duplicate' | 'untracked';

export interface UploadItem {
  key: string;
  filename: string;
  phase: UploadPhase;
  job: Job | null;
  error: string | null;
  warnings: JobWarning[];
}

// Each followed job holds an HTTP connection; browsers allow ~6 per origin over HTTP/1.1.
const MAX_SUBSCRIPTIONS = 3;

let nextKey = 0;

function newItem(filename: string, phase: UploadPhase, error: string | null = null): UploadItem {
  nextKey += 1;
  return { key: `upload-${nextKey}`, filename, phase, job: null, error, warnings: [] };
}

function finished(job: Job): Pick<UploadItem, 'phase' | 'job' | 'error' | 'warnings'> {
  return {
    phase: job.status === 'succeeded' ? 'succeeded' : 'failed',
    job,
    error: job.status === 'failed' ? (job.error_message ?? 'Xử lý tài liệu thất bại.') : null,
    warnings: job.warnings ?? [],
  };
}

/** Whether some file is still being sent to the server (lost if the page is left now). */
export function hasUnsentUploads(items: readonly UploadItem[]): boolean {
  return items.some((item) => item.phase === 'uploading');
}

export interface Uploads {
  items: UploadItem[];
  addFiles: (files: readonly File[]) => void;
  /** Shows progress of a job started elsewhere (re-indexing). */
  track: (job: Job, filename: string) => void;
  dismiss: (key: string) => void;
  clearFinished: () => void;
}

/**
 * Uploads files one at a time, then follows each processing job through /api/jobs/{id}/events.
 * `onSettled` runs whenever a document was stored or finished processing, so lists can refresh.
 */
export function useUploads(onSettled: () => void): Uploads {
  const [items, setItems] = useState<UploadItem[]>([]);
  const subscriptions = useRef(new Map<string, () => void>());
  const queue = useRef(Promise.resolve());
  const settledRef = useRef(onSettled);
  useEffect(() => {
    settledRef.current = onSettled;
  }, [onSettled]);

  const update = useCallback((key: string, patch: Partial<UploadItem>) => {
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...patch } : item)));
  }, []);

  const upload = useCallback(
    async (item: UploadItem, file: File) => {
      try {
        const { job, duplicate } = await uploadDocument(file);
        if (duplicate) update(item.key, { phase: 'duplicate', job });
        else if (!job) update(item.key, { phase: 'succeeded' });
        else if (job.status === 'succeeded' || job.status === 'failed') update(item.key, finished(job));
        else update(item.key, { phase: 'processing', job });
      } catch (caught) {
        update(item.key, { phase: 'failed', error: caught instanceof Error ? caught.message : String(caught) });
      }
      settledRef.current();
    },
    [update],
  );

  const addFiles = useCallback(
    (files: readonly File[]) => {
      const added = files.map((file) =>
        isAcceptedFile(file.name)
          ? { item: newItem(file.name, 'uploading'), file }
          : { item: newItem(file.name, 'failed', 'Định dạng không hỗ trợ (chỉ PDF, DOCX, XLS/XLSX, PNG, JPG).'), file: null },
      );
      setItems((current) => [...added.map(({ item }) => item), ...current]);
      for (const { item, file } of added) {
        if (file) queue.current = queue.current.then(() => upload(item, file));
      }
    },
    [upload],
  );

  const track = useCallback((job: Job, filename: string) => {
    setItems((current) => [{ ...newItem(filename, 'processing'), job }, ...current]);
  }, []);

  // Follow processing jobs, at most MAX_SUBSCRIPTIONS at a time; the others wait with their last known state.
  useEffect(() => {
    const active = subscriptions.current;
    for (const item of items) {
      if (item.phase !== 'processing' || !item.job || active.has(item.key)) continue;
      if (active.size >= MAX_SUBSCRIPTIONS) break;
      const stop = () => active.delete(item.key);
      active.set(
        item.key,
        subscribeJob(item.job.id, {
          onProgress: (job) => update(item.key, { job, warnings: job.warnings ?? [] }),
          onDone: (job) => {
            stop();
            update(item.key, finished(job));
            settledRef.current();
          },
          onError: (message) => {
            stop();
            update(item.key, { phase: 'untracked', error: message });
            settledRef.current();
          },
        }),
      );
    }
  }, [items, update]);

  // Leaving the page aborts files still being sent (processing continues on the server): ask first.
  const sending = hasUnsentUploads(items);
  useEffect(() => {
    if (!sending) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [sending]);

  useEffect(() => {
    const active = subscriptions.current;
    return () => {
      active.forEach((close) => close());
      active.clear();
    };
  }, []);

  const dismiss = useCallback((key: string) => {
    subscriptions.current.get(key)?.();
    subscriptions.current.delete(key);
    setItems((current) => current.filter((item) => item.key !== key));
  }, []);

  const clearFinished = useCallback(() => {
    setItems((current) => current.filter((item) => item.phase === 'uploading' || item.phase === 'processing'));
  }, []);

  return { items, addFiles, track, dismiss, clearFinished };
}
