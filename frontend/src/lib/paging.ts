import type { Page } from '../api/types';

export type PageFetcher<T> = (page: number, pageSize: number) => Promise<Page<T>>;

/** The API's maximum `page_size`. */
export const MAX_PAGE_SIZE = 100;

/**
 * Reloads the first `count` items, in as many maximum-size pages as needed, so a refresh keeps everything the
 * user has already loaded with "load more" (a single request is capped at MAX_PAGE_SIZE).
 */
export async function fetchFirst<T extends { id: string }>(
  fetchPage: PageFetcher<T>,
  count: number,
  maxPageSize = MAX_PAGE_SIZE,
): Promise<Page<T>> {
  if (count <= maxPageSize) return fetchPage(1, count);
  const pages = await Promise.all(
    Array.from({ length: Math.ceil(count / maxPageSize) }, (_, i) => fetchPage(i + 1, maxPageSize)),
  );
  // Items shift between pages when something is added or removed meanwhile: drop the repeats.
  const seen = new Set<string>();
  const items = pages
    .flatMap((page) => page.items)
    .filter((item) => !seen.has(item.id) && seen.add(item.id))
    .slice(0, count);
  const last = pages[pages.length - 1] as Page<T>;
  return { items, total: last.total, page: 1, page_size: count };
}
