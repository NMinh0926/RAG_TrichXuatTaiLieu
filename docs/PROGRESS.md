# PROGRESS

## Trạng thái

| Giai đoạn | Trạng thái |
|---|---|
| 0 — Khảo sát & baseline | **Xong** (29/09/2026) |
| 1 — Lưu trữ PostgreSQL + object storage | **Xong** (29/09/2026) — SeaweedFS thay MinIO |
| 2 — Chất lượng câu trả lời | **Xong** (30/09/2026) — bge-m3, map-reduce, SSE, hội thoại |
| 3 — Tốc độ ingest | **Xong** (30/09/2026) — RQ worker, Tesseract, bbox |
| 4 — Giao diện | **Xong** (30/09/2026) — React + TS + Tailwind, nginx trong compose |
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

## Giai đoạn 1 — đã làm

- **Compose**: `postgres` (17-alpine, volume `pg_data`, tự tạo DB `<db>_test` cho test), `seaweedfs`
  (S3 API, credential từ `S3_ACCESS_KEY/S3_SECRET_KEY`, tự tạo bucket khi khởi động — `docker/seaweedfs/start.sh`),
  `chroma` (server 1.5.9, volume `chroma_data`), `init` (một lần: `alembic upgrade head` + kiểm tra storage/Chroma),
  `api` (chờ `init`). Mọi dữ liệu nằm trong Docker volume. Fallback credential dev trong compose chỉ để chạy local.
- **Schema** (SQLAlchemy 2 + Alembic `0001`): `documents` (sha256 unique, status pending/processing/ready/failed,
  object_key, page_count, chunk_count), `chunks` (ordinal, page/sheet, content, char_start/char_end, bboxes JSONB),
  `ingest_jobs` (status, progress, step, warnings JSONB, started/finished), `conversations`, `messages`
  (sources JSONB, `seq` identity để giữ thứ tự). ID vector trong Chroma = `chunks.id`; metadata `document_id` = `documents.id`.
- **Storage**: `app/storage/base.py` (Protocol put/get/read/delete/presigned_url/ping), `app/storage/s3.py` (boto3,
  path-style, SigV4). Loader nhận bytes; không còn ghi file người dùng ra đĩa (`UPLOAD_DIR` đã bỏ).
- **Upload**: `DocumentService` — sha256 → trùng thì trả document cũ (`duplicate: true`), không xử lý lại;
  giữ chỗ sha256 bằng bản ghi trước khi OCR (upload đồng thời cùng file an toàn); lỗi → xóa object/rows/vectors.
- **API mới** (dưới `/api/documents` thay vì `/documents` để sau này chung reverse proxy với frontend):
  `GET /api/documents?page&page_size&q`, `GET /api/documents/{id}`, `GET /api/documents/{id}/file` (stream, inline),
  `DELETE /api/documents/{id}`. `POST /api/documents/upload` giữ nguyên field cũ + `document_id`, `duplicate`.
  Bỏ endpoint phase 1 `POST /documents/ingest` (frontend không dùng).
- **Migrate**: `scripts/migrate_legacy.py` — đã chạy trên dữ liệu thật: 9 tài liệu liên kết vector cũ (không OCR lại,
  khôi phục đúng tên file gốc từ id cũ), 2 file trùng nội dung bỏ qua, 0 lỗi. `data/` cũ vẫn giữ nguyên.
- **Backup/restore**: `scripts/backup.ps1`, `scripts/restore.ps1` (+ `scripts/objects_transfer.py`). Đã thử: xóa 1 tài liệu
  → restore → tài liệu, file và vector quay lại.
- **Cách ly**: `scripts/isolation.py` — test/benchmark dùng DB `_test`, bucket `-scratch`, Chroma local; không chạm dữ liệu thật.
- Xóa script chẩn đoán cũ (`inspect_retrieval`, `debug_representation`, `reproduce_retrieval_gate`, `diagnose_upload`).
- Tests: 125 passed (storage stub + SeaweedFS thật, repository, API tài liệu, migrate, persistence 2 tiến trình).

## Giai đoạn 2 — đã làm

- **LLM**: `app/llm/client.py` — client chung API tương thích OpenAI (`/chat/completions`, stream SSE, `/models`);
  cấu hình `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL/LLM_TIMEOUT_SECONDS/LLM_MAX_TOKENS/LLM_TEMPERATURE/LLM_CONTEXT_TOKENS`,
  tùy chọn `LLM_SUMMARY_MODEL` cho bước map. Đã xóa client Ollama native. `/health/llm`, `scripts/check_llm.py`.
  Ollama host đặt `OLLAMA_CONTEXT_LENGTH=8192` (biến môi trường user Windows) vì API OpenAI không truyền num_ctx.
- **Ý định**: `app/services/intent.py` — quy tắc (tóm tắt/tổng quan/ý chính/overview…), câu mơ hồ ("nói về gì")
  mới hỏi LLM.
- **Tóm tắt map-reduce**: `app/services/summarizer.py` — toàn bộ chunk theo thứ tự (bỏ phần overlap nhờ char offset),
  gom nhóm vừa context, tóm tắt từng nhóm, gộp nhiều cấp nếu cần, bản cuối có cấu trúc Tổng quan / Các phần chính /
  Chi tiết và số liệu / Kết luận; cache bảng `document_summaries` (model + phiên bản prompt).
- **Retrieval**: A/B 4 cấu hình → chốt **bge-m3, chunk 512/64, TOP_K 6, không rerank** (reranker tùy chọn
  `RERANKER_MODEL`). `scripts/reindex.py` re-index toàn bộ từ storage.
- **Prompt**: trả lời đầy đủ, markdown, trích dẫn `[n]` theo đoạn ngữ cảnh; nguồn có chunk_id, document_id, trang,
  đoạn trích, bboxes.
- **Streaming + hội thoại**: `POST /api/chat` (SSE qua POST: meta/progress/token/replace/done/error),
  `GET/DELETE /api/conversations[/{id}]`; câu hỏi nối tiếp được LLM viết lại thành câu độc lập trước retrieval.
  `/api/questions/ask` giữ cho client cũ (non-stream).
- Số liệu: [benchmarks/stage2.md](benchmarks/stage2.md).

## Giai đoạn 3 — đã làm

- **Hàng đợi**: Redis + RQ (`app/services/jobs.py`, `app/worker.py`, service `worker` trong compose, SimpleWorker
  nạp model một lần). Upload `POST /api/documents` → 202 + job; `GET /api/jobs/{id}`, SSE `/api/jobs/{id}/events`;
  bảng `ingest_jobs` lưu %/bước/cảnh báo/thời gian từng bước. Worker khởi động lại tự đưa job dang dở vào hàng đợi.
  `POST /api/documents/upload` (cũ) chờ job xong để giữ tương thích. `POST /api/documents/{id}/reindex`.
- **OCR**: Tesseract `vie` thay PaddleOCR (CER 0,09% vs 10,7%; 2,3 vs 15,8 s/trang), OCR nhiều trang song song
  (`OCR_WORKERS`); chỉ OCR trang không có text layer; lỗi OCR một trang → cảnh báo, không hỏng file.
- **Bounding box**: text trang dựng từ từng từ (PyMuPDF / Tesseract TSV) → mỗi chunk lưu trang, char offset, bboxes
  (điểm PDF, 1 hình chữ nhật/dòng).
- **Embedding** theo batch có tiến độ; tự dùng GPU nếu torch thấy CUDA (image hiện là CPU).
- Số liệu: [benchmarks/stage3.md](benchmarks/stage3.md) — upload 4 MB 77,6 s (baseline 95–104 s), OCR 5,3 s
  (baseline ~60 s), RAM 3,1 GB (5,3 GB).

## Giai đoạn 4 — đã làm

- Frontend mới `frontend/`: React 19 + TypeScript + Vite + Tailwind 4, PDF.js (worker đóng gói), react-markdown.
  Đặc tả và hợp đồng API: [frontend-spec.md](frontend-spec.md). Sidebar (thư viện + lịch sử), chat streaming,
  viewer PDF bên phải có highlight bbox khi bấm `[n]`, upload kéo thả nhiều file với tiến độ thời gian thực,
  phạm vi hỏi, gợi ý nhanh, dark/light, responsive. 30 unit test (SSE parser, citation, bbox).
- Chạy bằng service `frontend` (nginx, cổng 127.0.0.1:8080, proxy `/api` + `/health`, SSE không buffer).

## Quyết định / ghi chú

- **MinIO → SeaweedFS** (người dùng duyệt): MinIO không còn image Docker công khai (Docker Hub gỡ, quay/ghcr cần đăng nhập).
  Code chỉ dùng S3 API nên đổi sang S3/R2 chỉ bằng cấu hình.
- **Chroma chế độ server** từ Giai đoạn 1: API và worker (Giai đoạn 3) cùng ghi vector; persistent client không an toàn đa tiến trình.
- Chunk migrate từ dữ liệu cũ chưa có char offset/bbox (char_start=0) — sẽ đầy đủ khi re-index (Giai đoạn 2/3).

- Không sửa logic ứng dụng trong Giai đoạn 0.
- Git: repo riêng trong `Bai_1`, nhánh `upgrade` (người dùng duyệt). `data/`, `backups/`, `.env` không commit.
- Quyết định người dùng cho các giai đoạn sau: hàng đợi **RQ + Redis**; frontend **React + TS + Vite + Tailwind**;
  làm liền các giai đoạn, tự chốt model theo số đo.

## Việc còn dở / đề xuất cho giai đoạn sau

- README chưa viết lại theo kiến trúc mới (làm ở Giai đoạn 6); lệnh hiện hành nằm trong CLAUDE.md.
- `.env` của người dùng vẫn đặt MiniLM / CHUNK 700/120 / TOP_K 4 (không được sửa .env): cần đổi theo `.env.example`
  rồi chạy `scripts/reindex.py --drop-other-collections`.
- Nút thắt ingest còn lại: embedding bge-m3 CPU (~0,6 chunk/s).
