/** Vietnamese messages for FastAPI request-validation errors (HTTP 422 `{"detail": [...]}`). */

interface ValidationIssue {
  type?: unknown;
  loc?: unknown;
  msg?: unknown;
  ctx?: unknown;
}

const FIELD_LABELS: Record<string, string> = {
  question: 'Câu hỏi',
  document_ids: 'Phạm vi hỏi',
  conversation_id: 'Cuộc trò chuyện',
  username: 'Tên đăng nhập',
  password: 'Mật khẩu',
  new_password: 'Mật khẩu mới',
  current_password: 'Mật khẩu hiện tại',
  role: 'Vai trò',
  q: 'Từ khóa tìm kiếm',
};

const VALUE_ERROR_PREFIX = 'Value error, ';

function fieldLabel(loc: unknown): string {
  const path = Array.isArray(loc) ? loc.filter((part): part is string => typeof part === 'string' && part !== 'body') : [];
  const field = path.at(-1);
  return field ? (FIELD_LABELS[field] ?? field) : 'Dữ liệu';
}

function limit(ctx: unknown, key: string): number | null {
  const value = ctx && typeof ctx === 'object' ? (ctx as Record<string, unknown>)[key] : undefined;
  return typeof value === 'number' ? value : null;
}

function issueMessage({ type, loc, msg, ctx }: ValidationIssue): string {
  const field = fieldLabel(loc);
  // Our own validators raise Vietnamese messages; Pydantic prefixes them with "Value error, ".
  if (type === 'value_error' && typeof msg === 'string' && msg.startsWith(VALUE_ERROR_PREFIX)) {
    const text = msg.slice(VALUE_ERROR_PREFIX.length);
    if (/[^\x00-\x7F]/.test(text)) return text;
  }
  const min = limit(ctx, 'min_length');
  const max = limit(ctx, 'max_length');
  switch (type) {
    case 'missing':
      return `Thiếu ${field.toLowerCase()}.`;
    case 'string_too_short':
      return min !== null ? `${field} cần ít nhất ${min} ký tự.` : `${field} quá ngắn.`;
    case 'string_too_long':
      return max !== null ? `${field} chỉ được tối đa ${max} ký tự.` : `${field} quá dài.`;
    case 'too_long':
      return max !== null ? `${field} chỉ được tối đa ${max} mục.` : `${field} có quá nhiều mục.`;
    case 'too_short':
      return min !== null ? `${field} cần ít nhất ${min} mục.` : `${field} có quá ít mục.`;
    default:
      return `${field} không hợp lệ.`;
  }
}

/** The first validation issue as a Vietnamese sentence, or null when `detail` is not a validation list. */
export function validationMessage(detail: unknown): string | null {
  if (!Array.isArray(detail)) return null;
  const first: unknown = detail[0];
  if (!first || typeof first !== 'object') return null;
  return issueMessage(first as ValidationIssue);
}
