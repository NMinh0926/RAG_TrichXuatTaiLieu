import type { DocumentStatus, JobStep, Source, SummaryProgress } from '../api/types';

export const STEP_LABELS: Record<JobStep, string> = {
  queued: 'Đang chờ',
  download: 'Đang tải file',
  extract: 'Đang đọc/OCR',
  chunk: 'Đang tách đoạn',
  embed: 'Đang tạo vector',
  index: 'Đang lưu chỉ mục',
  done: 'Hoàn tất',
};

export const STATUS_LABELS: Record<DocumentStatus, string> = {
  pending: 'Đang chờ',
  processing: 'Đang xử lý',
  ready: 'Sẵn sàng',
  failed: 'Lỗi',
};

export function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024)).toLocaleString('vi-VN')} KB`;
  return `${(bytes / (1024 * 1024)).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} MB`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' });
}

export type FileKind = 'pdf' | 'image' | 'other';

export function fileKind(filename: string, contentType = ''): FileKind {
  const name = filename.toLowerCase();
  if (contentType === 'application/pdf' || name.endsWith('.pdf')) return 'pdf';
  if (contentType.startsWith('image/') || /\.(png|jpe?g)$/.test(name)) return 'image';
  return 'other';
}

export const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.xls', '.xlsx', '.png', '.jpg', '.jpeg'];

export function isAcceptedFile(filename: string): boolean {
  const name = filename.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((extension) => name.endsWith(extension));
}

export function summaryProgressText(progress: SummaryProgress): string {
  const name = progress.document ?? 'tài liệu';
  switch (progress.stage) {
    case 'map':
      return progress.total
        ? `Đang tóm tắt phần ${(progress.done ?? 0) + 1}/${progress.total} của ${name}…`
        : `Đang tóm tắt ${name}…`;
    case 'merge':
      return `Đang gộp các phần tóm tắt của ${name}…`;
    case 'reduce':
      return `Đang viết bản tóm tắt ${name}…`;
    case 'cached':
      return `Dùng bản tóm tắt đã lưu của ${name}…`;
    case 'combine':
      return 'Đang tổng hợp tóm tắt các tài liệu…';
  }
}

export function sourceLocation(source: Source): string {
  if (source.page) return `trang ${source.page}`;
  if (source.sheet) return `sheet ${source.sheet}`;
  return 'toàn bộ tài liệu';
}
