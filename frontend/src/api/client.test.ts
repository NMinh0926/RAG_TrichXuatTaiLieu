import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, currentUser, login, onUnauthorized, request } from './client';

function respond(status: number, body: unknown = {}) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status })));
}

afterEach(() => vi.unstubAllGlobals());

describe('session handling', () => {
  it('signals an expired session on 401 and surfaces the API message', async () => {
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    respond(401, { error: 'unauthorized', detail: 'Vui lòng đăng nhập.' });
    await expect(request('/api/documents')).rejects.toMatchObject({ status: 401, message: 'Vui lòng đăng nhập.' });
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    await expect(request('/api/documents')).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('does not treat a failed login as an expired session', async () => {
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    respond(401, { error: 'invalid_credentials', detail: 'Sai tên đăng nhập hoặc mật khẩu.' });
    await expect(login('a', 'b')).rejects.toMatchObject({ code: 'invalid_credentials' });
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });

  it('reports no current user without a session', async () => {
    respond(401);
    await expect(currentUser()).resolves.toBeNull();
    respond(200, { id: '1', username: 'an', role: 'user', is_active: true, created_at: '' });
    await expect(currentUser()).resolves.toMatchObject({ username: 'an' });
  });
});
