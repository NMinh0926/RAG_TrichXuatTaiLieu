import { describe, expect, it, vi } from 'vitest';
import type { Page } from '../api/types';
import { fetchFirst, MAX_PAGE_SIZE } from './paging';

function server(count: number) {
  const all = Array.from({ length: count }, (_, i) => ({ id: `d${i}` }));
  return vi.fn(async (page: number, pageSize: number): Promise<Page<{ id: string }>> => {
    const size = Math.min(pageSize, MAX_PAGE_SIZE);
    return { items: all.slice((page - 1) * size, page * size), total: all.length, page, page_size: size };
  });
}

describe('fetchFirst', () => {
  it('uses a single request up to the maximum page size', async () => {
    const fetchPage = server(30);
    const result = await fetchFirst(fetchPage, 20);
    expect(fetchPage.mock.calls).toEqual([[1, 20]]);
    expect(result.items).toHaveLength(20);
    expect(result.total).toBe(30);
  });

  it('keeps more than 100 loaded items on refresh', async () => {
    const fetchPage = server(150);
    const result = await fetchFirst(fetchPage, 140);
    expect(fetchPage.mock.calls).toEqual([
      [1, 100],
      [2, 100],
    ]);
    expect(result.items.map((item) => item.id)).toEqual(Array.from({ length: 140 }, (_, i) => `d${i}`));
    expect(result.total).toBe(150);
  });

  it('drops items repeated across pages', async () => {
    const pages: Record<number, string[]> = { 1: ['a', 'b'], 2: ['b', 'c'] };
    const fetchPage = async (page: number) => ({ items: (pages[page] ?? []).map((id) => ({ id })), total: 3, page, page_size: 2 });
    const result = await fetchFirst(fetchPage, 3, 2);
    expect(result.items.map((item) => item.id)).toEqual(['a', 'b', 'c']);
  });
});
