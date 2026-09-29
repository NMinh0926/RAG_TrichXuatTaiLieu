# PROGRESS

## Trạng thái

| Giai đoạn | Trạng thái |
|---|---|
| 0 — Khảo sát & baseline | **Xong** (29/09/2026) |
| 1 — Lưu trữ PostgreSQL + MinIO | Chưa bắt đầu |
| 2 — Chất lượng câu trả lời | Chưa bắt đầu |
| 3 — Tốc độ ingest | Chưa bắt đầu |
| 4 — Giao diện | Chưa bắt đầu |
| 5 — Benchmark | Chưa bắt đầu |
| 6 — Hoàn thiện nội bộ | Chưa bắt đầu |

## Kiến trúc hiện tại (trước Giai đoạn 1)

- **Backend**: FastAPI 1 tiến trình (`app/`), chạy trong Docker (`compose.yaml`, service `api`,
  image `bai1-rag:local`, Python 3.11, torch/paddle CPU). Cổng 127.0.0.1:8000.
  `extra_hosts: host.docker.internal:host-gateway` để gọi Ollama trên host.
- **Frontend**: React 19 + Vite 7 + react-router 7, JavaScript (không TypeScript), 2 trang `/documents` (upload) và `/ask`
  (`frontend/src/pages`). Gọi API qua `VITE_API_URL`. Chạy bằng container dev `bai1-rag-frontend-demo`
  (node:22-alpine, `npm run dev`), **không nằm trong compose**. Không có danh sách tài liệu, xem file, lịch sử chat.
- **Lưu file**: `POST /api/documents/upload` ghi file vào `data/uploads/<uuid>.<ext>` (bind mount),
  giữ lại sau khi xử lý; tên gốc chỉ nằm trong metadata Chroma. Không có database quan hệ.
- **Vector**: ChromaDB PersistentClient ở `data/chroma`, collection `documents_<hash(model, chunk cfg)>`,
  cosine. ID chunk = `sha256(tên file + bytes)_<chunk_index>`; metadata: filename, page | sheet,
  source_type, file_type, chunk_index, document_id. Không có vị trí ký tự hay bounding box.
- **Luồng ingest** (đồng bộ trong request, `IngestionService.ingest`): kiểm tra đuôi/kích thước →
  hash → `load_file` (PDF: PyMuPDF từng trang, trang < 20 ký tự chữ/số thì render 200 dpi + PaddleOCR;
  DOCX, XLS/XLSX theo sheet, ảnh → OCR) → `clean_text` → `chunk_units` (token window, cap theo
  max_seq_length 128 của MiniLM → 126 token, overlap 22) → embedding MiniLM CPU → Chroma upsert +
  đọc lại xác minh → trả `{status, filename, chunks_created}`.
- **Luồng hỏi đáp** (`POST /api/questions/ask`): `RetrievalService.search_candidates` (Top-4 thô,
  không threshold) → nếu rỗng trả "Tôi không tìm thấy…" → `GenerationService.answer`: prompt
  `app/services/prompts.py`, Ollama `POST /api/chat` **stream=false** → phát hiện câu từ chối →
  parse `[Nguồn: …]`/`[n]` → `{answer, sources}`. Lỗi Ollama → 503/504/502 (không phải no-answer).
  `/questions/search` (legacy) còn lọc `RETRIEVAL_THRESHOLD` 0.55.
- **Cấu hình**: pydantic-settings đọc `.env` (`app/core/config.py`); mẫu ở `.env.example`
  (OLLAMA_*, EMBEDDING_MODEL, CHUNK_*, TOP_K, RETRIEVAL_THRESHOLD, CANDIDATE_MIN_RELEVANCE, OCR_LANG…).
- **Tests**: pytest, 111 test (unit + integration với OCR/embedding/Chroma thật); HTTP tới Ollama mock bằng
  `httpx.MockTransport`. Chạy trong Docker (lệnh ở CLAUDE.md).
- **Scripts**: `evaluate.py` (25 câu, Hit@k trước threshold), `check_ollama.py`, `diagnose_upload.py`,
  `reproduce_retrieval_gate.py`, `benchmark/`.

## Giai đoạn 0 — đã làm

- Viết `scripts/benchmark/make_test_pdf.py` (PDF tổng hợp 4,11 MB, 36 trang, 6 trang scan, 5 fact),
  `bench_ingest.py` (từng bước + end-to-end cold/warm + đo trong request), `bench_qa.py`
  (retrieval, TTFT bằng stream, tổng LLM, `/ask` thật, tóm tắt).
- Điền "Lệnh thường dùng" trong CLAUDE.md.
- Kết quả: [benchmarks/baseline.md](benchmarks/baseline.md).

## Số liệu chính (baseline)

- Ingest 4,11 MB / 36 trang: **upload cold 118–124 s, warm 95–104 s**; OCR 6 trang chiếm 88,9 s trong request.
- Hỏi đáp: retrieval 0,03–0,05 s; TTFT warm ~1,2 s, cold 11,9 s; ~10 token/s; câu fact `/ask` 1,8–5,6 s.
- Tóm tắt: `/ask` 38,9 s, 1 062 ký tự, chỉ dựa 4/174 chunk.
- Chất lượng: 3/5 fact đúng; 1 miss do Top-4 (hạng 6), 1 miss do OCR mất dấu tiếng Việt.

## 3 nút thắt tốc độ

1. **OCR CPU trong request**: 8–15 s/trang scan, ~90% thời gian ingest, chạy đồng bộ trong HTTP request
   (client chờ 1,5–2 phút cho 4 MB). Chậm thêm ~2× khi chạy trong thread đã khởi tạo engine.
2. **LLM 7B không vừa GPU 4 GB** (58% CPU / 42% GPU): ~10 token/s, cold load 10,7 s; câu dài
   (tóm tắt 429 token) 40–60 s; API không stream nên người dùng chờ hết mới thấy chữ.
3. **Nạp model trong request đầu + bộ nhớ**: ~20 s nạp MiniLM + PaddleOCR lazy trong upload đầu tiên;
   tiến trình backend đỉnh 5,3 GB trên VM Docker 8 GB (OCR + embedding cùng tiến trình web).

## Vì sao câu tóm tắt ngắn

1. Tóm tắt đi chung đường RAG: chỉ Top-4 chunk (2,3% tài liệu, 4/36 trang, ~1 750 ký tự) được gửi LLM.
2. Chunk bị cap 126 token do MiniLM max_seq_length=128 → mỗi đoạn rất nhỏ.
3. System prompt yêu cầu "rõ ràng, ngắn gọn" và "không lặp lại nguyên văn".
4. Không có phân loại ý định / map-reduce; num_ctx 8192 nhưng prompt chỉ ~800 token.

## Quyết định / ghi chú

- Không sửa logic ứng dụng trong Giai đoạn 0.
- **Git chưa commit**: repo git hiện ở `C:/Users/NhatMinh/Documents` (cả thư mục Documents, có file
  dự án khác đang staged); `Bai_1` chưa được track. Cần người dùng chọn: `git init` riêng trong `Bai_1`
  (khuyến nghị) rồi tạo nhánh `upgrade`, hay dùng repo Documents.

## Việc còn dở / đề xuất cho giai đoạn sau

- Git: chờ người dùng quyết định (xem trên).
- Giai đoạn 2/3 cần xét: model OCR hỗ trợ đầy đủ dấu tiếng Việt; tăng Top-K + rerank cho tài liệu dài;
  map-reduce cho tóm tắt; streaming.
- Giai đoạn 3: điều tra OCR chậm theo thread (số luồng Paddle), đưa OCR/embedding ra worker nền.
- Frontend chưa nằm trong compose (Giai đoạn 4).
