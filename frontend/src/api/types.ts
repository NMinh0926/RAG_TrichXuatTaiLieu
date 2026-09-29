export type DocumentStatus = 'pending' | 'processing' | 'ready' | 'failed';
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed';
export type JobStep = 'queued' | 'download' | 'extract' | 'chunk' | 'embed' | 'index' | 'done';

export interface JobWarning {
  page: number | null;
  step: string;
  error: string;
}

export interface Job {
  id: string;
  document_id: string;
  status: JobStatus;
  progress: number;
  step: JobStep;
  error_code: string | null;
  error_message: string | null;
  warnings: JobWarning[] | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface DocumentInfo {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  page_count: number | null;
  chunk_count: number;
  status: DocumentStatus;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  latest_job?: Job | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Submission {
  document: DocumentInfo;
  job: Job | null;
  duplicate: boolean;
}

/** Highlight rectangle in PDF points, origin at the top-left corner of the page. */
export interface Bbox {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Source {
  n: number;
  chunk_id: string | null;
  document_id: string | null;
  filename: string | null;
  page: number | null;
  sheet: string | null;
  excerpt: string | null;
  bboxes: Bbox[] | null;
}

export type Intent = 'qa' | 'summary';

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface StoredMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources: Source[] | null;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: StoredMessage[];
}

export interface ChatRequest {
  question: string;
  conversation_id?: string;
  document_ids?: string[];
}

export interface SummaryProgress {
  stage: 'map' | 'merge' | 'reduce' | 'cached' | 'combine';
  done?: number;
  total?: number;
  document?: string;
}

/** Events of the POST /api/chat stream, discriminated by the SSE event name. */
export type ChatEvent =
  | { kind: 'meta'; data: { conversation_id?: string; user_message_id?: string; intent?: Intent; question?: string } }
  | { kind: 'progress'; data: SummaryProgress }
  | { kind: 'token'; data: { text: string } }
  | { kind: 'replace'; data: { text: string } }
  | { kind: 'done'; data: { message_id: string; answer: string; sources: Source[]; intent: Intent } }
  | { kind: 'error'; data: { error: string; detail: string; status: number } };

export interface LlmHealth {
  status: string;
  model: string;
  model_available: boolean;
}
