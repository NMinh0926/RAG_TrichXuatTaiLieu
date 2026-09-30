import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchFirst, type PageFetcher } from '../lib/paging';

export interface PagedList<T> {
  items: T[];
  total: number;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  loadMore: () => void;
  /** Reloads what is currently shown without a loading state (after a change or while polling). */
  refresh: () => void;
  remove: (id: string) => void;
}

/** "Load more" list over a paginated endpoint; a new `fetchPage` identity (e.g. new search) resets it. */
export function usePagedList<T extends { id: string }>(fetchPage: PageFetcher<T>, pageSize: number): PagedList<T> {
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const itemsRef = useRef<T[]>([]);
  const requestRef = useRef(0);

  const apply = useCallback((next: T[], nextTotal: number) => {
    itemsRef.current = next;
    setItems(next);
    setTotal(nextTotal);
    setError(null);
  }, []);

  const load = useCallback(
    async (mode: 'reset' | 'more' | 'refresh') => {
      const request = ++requestRef.current;
      const loaded = itemsRef.current;
      if (mode === 'reset') setLoading(true);
      if (mode === 'more') setLoadingMore(true);
      try {
        if (mode === 'more') {
          const result = await fetchPage(Math.floor(loaded.length / pageSize) + 1, pageSize);
          if (request !== requestRef.current) return;
          const known = new Set(loaded.map((item) => item.id));
          apply([...loaded, ...result.items.filter((item) => !known.has(item.id))], result.total);
        } else {
          const size = mode === 'refresh' ? Math.max(pageSize, loaded.length) : pageSize;
          const result = await fetchFirst(fetchPage, size);
          if (request !== requestRef.current) return;
          apply(result.items, result.total);
        }
      } catch (caught) {
        if (request === requestRef.current) setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        if (request === requestRef.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [fetchPage, pageSize, apply],
  );

  useEffect(() => {
    void load('reset');
  }, [load]);

  const remove = useCallback((id: string) => {
    itemsRef.current = itemsRef.current.filter((item) => item.id !== id);
    setItems(itemsRef.current);
    setTotal((current) => Math.max(0, current - 1));
  }, []);

  return {
    items,
    total,
    loading,
    loadingMore,
    error,
    loadMore: useCallback(() => void load('more'), [load]),
    refresh: useCallback(() => void load('refresh'), [load]),
    remove,
  };
}
