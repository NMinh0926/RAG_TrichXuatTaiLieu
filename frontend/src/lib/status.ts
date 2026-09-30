import type { Check } from '../api/types';

/** Display names of the components reported by GET /api/system/status, in display order. */
export const CHECK_LABELS: Record<string, string> = {
  database: 'Cơ sở dữ liệu (PostgreSQL)',
  object_storage: 'Lưu trữ file (S3)',
  vector_store: 'Chỉ mục vector (Chroma)',
  queue: 'Hàng đợi (Redis)',
  worker: 'Worker xử lý tài liệu',
  llm: 'Mô hình ngôn ngữ (LLM)',
  llm_summary: 'Mô hình tóm tắt (LLM_SUMMARY_MODEL)',
};

const ERROR_LABELS: Record<string, string> = {
  no_worker: 'không có worker đang chạy',
  llm_unavailable: 'không kết nối được',
  llm_model_missing: 'model chưa được tải',
  llm_timeout: 'quá thời gian chờ',
  queue_unavailable: 'không kết nối được',
};

export function orderedChecks(checks: Record<string, Check>): [string, Check][] {
  const rank = (name: string) => {
    const index = Object.keys(CHECK_LABELS).indexOf(name);
    return index === -1 ? Number.MAX_SAFE_INTEGER : index;
  };
  return Object.entries(checks).sort(([a], [b]) => rank(a) - rank(b));
}

export function describeCheck(check: Check): string {
  if (check.ok) return check.ms !== undefined ? `Hoạt động · ${Math.round(check.ms)} ms` : 'Hoạt động';
  const reason = check.error ? (ERROR_LABELS[check.error] ?? check.error) : 'không rõ nguyên nhân';
  return `Lỗi: ${reason}`;
}
