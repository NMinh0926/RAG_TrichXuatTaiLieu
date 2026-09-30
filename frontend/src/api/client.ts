/** Typed client for the backend API (same origin). Every HTTP call goes through `request()`. */
import { validationMessage } from '../lib/errors';
import { readSse } from '../lib/sse';
import type {
  ChatEvent,
  ChatRequest,
  Conversation,
  ConversationDetail,
  DocumentInfo,
  Job,
  LlmHealth,
  Page,
  Role,
  Session,
  Submission,
  SystemStatus,
  User,
} from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

const STATUS_MESSAGES: Record<number, string> = {
  401: 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.',
  403: 'Bạn không có quyền thực hiện thao tác này.',
  404: 'Không tìm thấy dữ liệu yêu cầu.',
  409: 'Thao tác bị từ chối vì dữ liệu đang được xử lý.',
  413: 'File quá lớn.',
  415: 'Định dạng file không được hỗ trợ.',
  422: 'Dữ liệu gửi lên không hợp lệ.',
  429: 'Quá nhiều yêu cầu; vui lòng thử lại sau ít phút.',
  500: 'Lỗi máy chủ (500). Vui lòng thử lại hoặc báo quản trị viên.',
  503: 'Dịch vụ tạm thời không sẵn sàng.',
};

async function errorFromResponse(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let message = STATUS_MESSAGES[response.status] ?? `Lỗi máy chủ (${response.status}).`;
  try {
    const body: unknown = await response.json();
    if (body && typeof body === 'object') {
      const { error, detail } = body as { error?: unknown; detail?: unknown };
      if (typeof error === 'string') code = error;
      // Our API errors carry a Vietnamese `detail`, except the catch-all 500 whose detail is meant for operators.
      // FastAPI's own errors ("Not Found", validation lists) have no `error` key.
      if (typeof detail === 'string' && typeof error === 'string' && error !== 'internal_error') message = detail;
      else if (response.status === 422) message = validationMessage(detail) ?? message;
    }
  } catch {
    // Non-JSON body (e.g. a proxy error page): keep the status-based message.
  }
  return new ApiError(response.status, code, message);
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

type UnauthorizedListener = () => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

/** Called when any request comes back 401 (session expired or revoked); returns an unsubscribe function. */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

/**
 * Single entry point for HTTP calls. Authentication is the HttpOnly session cookie the browser sends on
 * same-origin requests (also for pdf.js and EventSource). Throws ApiError when not ok.
 */
export async function request(path: string, init: RequestInit = {}): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new ApiError(0, 'network_error', 'Không kết nối được máy chủ. Hãy kiểm tra backend đang chạy.');
  }
  if (response.status === 401 && path !== '/api/auth/login') unauthorizedListeners.forEach((listener) => listener());
  if (!response.ok) throw await errorFromResponse(response);
  return response;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await request(path, init);
  return (await response.json()) as T;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  return search.toString();
}

// Documents

export function listDocuments(page: number, pageSize: number, q = ''): Promise<Page<DocumentInfo>> {
  return requestJson(`/api/documents?${query({ page, page_size: pageSize, q })}`);
}

export function getDocument(id: string): Promise<DocumentInfo> {
  return requestJson(`/api/documents/${id}`);
}

export function uploadDocument(file: File): Promise<Submission> {
  const form = new FormData();
  form.append('file', file);
  return requestJson('/api/documents', { method: 'POST', body: form });
}

export async function deleteDocument(id: string): Promise<void> {
  await request(`/api/documents/${id}`, { method: 'DELETE' });
}

export function reindexDocument(id: string): Promise<Job> {
  return requestJson(`/api/documents/${id}/reindex`, { method: 'POST' });
}

export function setDocumentShared(id: string, isShared: boolean): Promise<DocumentInfo> {
  return requestJson(`/api/documents/${id}`, jsonInit('PATCH', { is_shared: isShared }));
}

export function documentFileUrl(id: string): string {
  return `/api/documents/${id}/file`;
}

// Jobs

export interface JobHandlers {
  onProgress: (job: Job) => void;
  onDone: (job: Job) => void;
  onError: (message: string) => void;
}

/** Follows /api/jobs/{id}/events; returns a function that closes the stream. */
export function subscribeJob(id: string, handlers: JobHandlers): () => void {
  const source = new EventSource(`/api/jobs/${id}/events`);
  source.addEventListener('progress', (event) => handlers.onProgress(JSON.parse(event.data) as Job));
  source.addEventListener('done', (event) => {
    source.close();
    handlers.onDone(JSON.parse(event.data) as Job);
  });
  source.addEventListener('error', (event) => {
    if (event instanceof MessageEvent) {
      // Error event sent by the server, e.g. {"error": "stream_timeout"}.
      source.close();
      const { error } = JSON.parse(event.data) as { error: string };
      handlers.onError(error === 'stream_timeout' ? 'Quá thời gian chờ xử lý.' : 'Không theo dõi được tiến trình xử lý.');
    } else if (source.readyState === EventSource.CLOSED) {
      handlers.onError('Mất kết nối khi theo dõi tiến trình xử lý.');
    }
    // Otherwise the browser reconnects on its own.
  });
  return () => source.close();
}

// Chat

const CHAT_EVENTS = new Set<string>(['meta', 'progress', 'token', 'replace', 'done', 'error']);

/** POST /api/chat and dispatch its Server-Sent Events until the stream ends or `signal` aborts. */
export async function streamChat(body: ChatRequest, onEvent: (event: ChatEvent) => void, signal: AbortSignal): Promise<void> {
  const response = await request('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.body) throw new ApiError(0, 'no_stream', 'Trình duyệt không hỗ trợ đọc luồng phản hồi.');
  await readSse(response.body, ({ event, data }) => {
    if (CHAT_EVENTS.has(event)) onEvent({ kind: event, data: JSON.parse(data) } as ChatEvent);
  });
}

export function listConversations(page: number, pageSize: number): Promise<Page<Conversation>> {
  return requestJson(`/api/conversations?${query({ page, page_size: pageSize })}`);
}

export function getConversation(id: string): Promise<ConversationDetail> {
  return requestJson(`/api/conversations/${id}`);
}

export async function deleteConversation(id: string): Promise<void> {
  await request(`/api/conversations/${id}`, { method: 'DELETE' });
}

// Session and accounts

export function login(username: string, password: string): Promise<Session> {
  return requestJson('/api/auth/login', jsonInit('POST', { username, password }));
}

export async function logout(): Promise<void> {
  await request('/api/auth/logout', { method: 'POST' });
}

/** The signed-in user, or null without a valid session. */
export async function currentUser(): Promise<User | null> {
  const response = await fetch('/api/auth/me').catch(() => null);
  if (response?.status === 401) return null;
  if (!response?.ok) throw response ? await errorFromResponse(response) : new ApiError(0, 'network_error', 'Không kết nối được máy chủ.');
  return (await response.json()) as User;
}

export function changePassword(currentPassword: string, newPassword: string): Promise<Session> {
  return requestJson('/api/auth/password', jsonInit('POST', { current_password: currentPassword, new_password: newPassword }));
}

export function listUsers(): Promise<User[]> {
  return requestJson('/api/users');
}

export function createUser(username: string, password: string, role: Role): Promise<User> {
  return requestJson('/api/users', jsonInit('POST', { username, password, role }));
}

export function updateUser(id: string, changes: { password?: string; role?: Role; is_active?: boolean }): Promise<User> {
  return requestJson(`/api/users/${id}`, jsonInit('PATCH', changes));
}

// Health and status

export function getLlmHealth(): Promise<LlmHealth> {
  return requestJson('/health/llm');
}

export function getSystemStatus(): Promise<SystemStatus> {
  return requestJson('/api/system/status');
}
