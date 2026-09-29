# Giai đoạn 3 — Tốc độ ingest (30/09/2026)

Cùng PDF 4,11 MB / 36 trang (30 trang text, 6 trang scan) như baseline; cấu hình Giai đoạn 2
(bge-m3, chunk 512). Số liệu thô: `stage3_ingest.json`, `stage3_ocr_ab.json`.

## OCR: PaddleOCR → Tesseract

6 trang scan tiếng Việt có văn bản gốc (so bằng khoảng cách Levenshtein, CER = lỗi ký tự / độ dài):

| Engine | CER | Giây/trang (1 luồng) | Từ khóa bộ evaluation (ảnh/scan tiếng Anh) |
|---|---:|---:|---:|
| PaddleOCR PP-OCRv5 latin (cũ) | **10,7%** | 15,8 | 8/8 |
| Tesseract 5.3 `vie` | **0,09%** | 2,3 | 8/8 |

PaddleOCR làm rơi các chữ có dấu chồng ("vật" → "vt", "mở cửa" → "m ca"); Tesseract đọc đúng.
Tesseract chạy như tiến trình riêng nên OCR được nhiều trang song song (`OCR_WORKERS`, mặc định 4).
Đã gỡ PaddleOCR và 27 gói phụ thuộc chỉ nó dùng (opencv, paddlex, modelscope…): image backend còn ~0,65 GB.

## Upload end-to-end (warm, cùng file)

| | Baseline (G0) | G2 (bge-m3 + Paddle, trong request) | **G3 (worker, Tesseract song song)** |
|---|---:|---:|---:|
| Tổng (upload → sẵn sàng) | 95–104 s | 140 s | **77,6 s** |
| Đọc PDF + OCR 6 trang | ~60 s (OCR 14,8 s/trang trong request) | 60 s | **5,3 s** (OCR 2,5 s/trang, 4 song song) |
| Chunk | 0,1 s | 0,07 s | 0,07 s |
| Embedding | 4,9 s (MiniLM, 174 chunk) | 78 s (bge-m3, 49 chunk) | 72 s |
| Ghi Chroma | 0,2 s | 0,1 s | 0,1 s |
| RAM đỉnh tiến trình | 5,3 GB | 6,2 GB | **3,1 GB** |
| Nạp model lần đầu | ~20 s | ~73 s | ~20 s (một lần khi worker khởi động) |

- Upload giờ trả về ngay (`POST /api/documents` → 202 + `job_id`); tiến độ % và bước hiện tại trong
  `ingest_jobs`, theo dõi qua `GET /api/jobs/{id}` hoặc SSE `/api/jobs/{id}/events`. Thời gian từng bước
  lưu ở `ingest_jobs.timings`.
- Nút thắt còn lại: **embedding bge-m3 trên CPU (~0,6 chunk/s)**. Code tự dùng GPU khi torch thấy CUDA,
  nhưng image dùng torch CPU vì GPU 4 GB của máy này dành cho LLM (qwen2.5:7b đã chiếm ~3,2 GB).
  Trên máy có GPU riêng cho embedding, đổi sang wheel CUDA (`--index-url .../whl/cu128`) là đủ.
- Lỗi OCR từng trang không làm hỏng cả file: trang lỗi dùng text layer (nếu có) và được ghi vào
  `ingest_jobs.warnings`.
- Mỗi chunk lưu trang, vị trí ký tự và bounding box (điểm PDF) — từ PyMuPDF với trang có text, từ Tesseract
  (quy đổi pixel 200 dpi → điểm) với trang scan.
