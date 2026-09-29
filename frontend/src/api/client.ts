/** Typed client for the backend API (same origin). Every HTTP call goes through `request()`. */
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
  Submission,
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
  404: 'Không tìm thấy dữ liệu yêu cầu.',
  409: 'Thao tác bị từ chối vì dữ liệu đang được xử lý.',
  413: 'File quá lớn.',
  415: 'Định dạng file không được hỗ trợ.',
  422: 'Dữ liệu gửi lên không hợp lệ.',
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
      // Our API errors carry a Vietnamese `detail`; FastAPI's own errors ("Not Found", validation lists) do not.
      if (typeof detail === 'string' && typeof error === 'string') message = detail;
    }
  } catch {
    // Non-JSON body (e.g. a proxy error page): keep the status-based message.
  }
  return new ApiError(response.status, code, message);
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

/** Single entry point for HTTP calls (a place to add auth headers later). Throws ApiError when not ok. */
export async function request(path: string, init: RequestInit = {}): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new ApiError(0, 'network_error', 'Không kết nối được máy chủ. Hãy kiểm tra backend đang chạy.');
  }
  if (!response.ok) throw await errorFromResponse(response);
  return response;
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

// Health

export function getLlmHealth(): Promise<LlmHealth> {
  return requestJson('/health/llm');
}
