import { describe, expect, it } from 'vitest';
import { describeCheck, orderedChecks } from './status';

describe('system status', () => {
  it('orders known components first and keeps unknown ones', () => {
    const names = orderedChecks({ llm: { ok: true }, extra: { ok: true }, database: { ok: false } }).map(([name]) => name);
    expect(names).toEqual(['database', 'llm', 'extra']);
  });

  it('describes healthy and failing checks', () => {
    expect(describeCheck({ ok: true, ms: 3.4 })).toBe('Hoạt động · 3 ms');
    expect(describeCheck({ ok: false, error: 'no_worker' })).toBe('Lỗi: không có worker đang chạy');
    expect(describeCheck({ ok: false, error: 'OperationalError' })).toBe('Lỗi: OperationalError');
    expect(describeCheck({ ok: false })).toBe('Lỗi: không rõ nguyên nhân');
  });
});
