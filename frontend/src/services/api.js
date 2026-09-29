const API_URL = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

async function request(path, options) {
  let response;
  try {
    response = await fetch(`${API_URL}${path}`, options);
  } catch {
    throw new Error('Không thể kết nối backend. Kiểm tra backend đang chạy và VITE_API_URL.');
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`Backend trả phản hồi không hợp lệ (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail
      : Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('; ') : null;
    throw new Error(detail || `Yêu cầu thất bại (HTTP ${response.status}). Vui lòng thử lại.`);
  }
  return data;
}

export function uploadDocument(file) {
  const body = new FormData();
  body.append('file', file);
  return request('/api/documents/upload', { method: 'POST', body });
}

export function askQuestion(question) {
  return request('/api/questions/ask', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }) });
}
