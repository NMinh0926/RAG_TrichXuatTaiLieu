# PROGRESS

## Trạng thái

| Giai đoạn | Trạng thái |
|---|---|
| 0 — Khảo sát & baseline | **Xong** (29/09/2026) |
| 1 — Lưu trữ PostgreSQL + object storage | **Xong** (29/09/2026) — SeaweedFS thay MinIO |
| 2 — Chất lượng câu trả lời | **Xong** (30/09/2026) — bge-m3, map-reduce, SSE, hội thoại |
| 3 — Tốc độ ingest | **Xong** (30/09/2026) — RQ worker, Tesseract, bbox |
| 4 — Giao diện | **Xong** (30/09/2026) — React + TS + Tailwind, nginx trong compose |
| 5 — Benchmark | **Xong** (30/09/2026) — [report.md](benchmarks/report.md) |
| 6 — Hoàn thiện nội bộ | **Xong** (30/09/2026) — đăng nhập, chia sẻ, giới hạn, log JSON, trạng thái, README |

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

## Giai đoạn 5 — đã làm

- Scripts: `make_corpus` (PDF text/scan ~1/5/10/50/100 MB, cố định), `bench_corpus` (upload thật → job, thời gian từng bước,
  RAM/CPU container từ cgroup, dung lượng PostgreSQL/S3/Chroma), `bench_load` (1/5/10 người chat SSE đồng thời),
  `charts` (SVG không cần thư viện), `resources` (lấy mẫu cgroup). `evaluate` thêm recall@1/3/5/K/10.
  GPU lấy mẫu bằng `nvidia-smi` trên host trong lúc đo.
- Báo cáo [benchmarks/report.md](benchmarks/report.md) + JSON `stage5_*.json` + `charts/*.svg`. Mẫu bộ câu hỏi riêng:
  `evaluation/custom_questions.example.json` (tài liệu/câu hỏi riêng để trong `evaluation/custom/`, không commit).
- Số liệu chính: ingest **~1,25 s/trang PDF text** (embedding bge-m3 CPU chiếm ~99%), **~1,5 s/trang PDF scan**;
  100 MB text (1 102 trang) 22,9 ph, 100 MB scan (166 trang) 4,1 ph; RAM đỉnh ≤ 3,0 GB; dữ liệu sinh ra ≈ 1,05–1,4 × file.
  Hỏi đáp `qwen2.5:7b`: 1 người TTFT 4,5 s; 10 người TTFT median 25,5 s / p95 46 s (Ollama tuần tự, ~6 token/s, model
  không vừa 4 GB VRAM). `qwen2.5:3b`: 10 người TTFT 2,8 s nhưng đúng 64,5% so với 96,8% → giữ 7b.
  Chất lượng 7b: recall@1 = 100%, đúng 30/31, từ chối đúng 7/8 (39 câu).
- Đề xuất đã áp dụng ở G6: `MAX_PDF_PAGES=300`, `MAX_UPLOAD_MB=100`.

## Giai đoạn 6 — đã làm

- **Tài khoản** (không thư viện mới): mật khẩu scrypt, token ký HMAC-SHA256 (`app/core/security.py`) trong cookie
  `rag_session` HttpOnly + SameSite=Strict hoặc `Authorization: Bearer`; `token_version` tăng khi đổi mật khẩu/khóa
  → thu hồi mọi token. API: `/api/auth/{login,logout,me,password}`, `/api/users` (admin). Tạo tài khoản:
  `scripts/create_user.py` hoặc `INITIAL_ADMIN_USERNAME/PASSWORD` (service `init`, chỉ khi chưa có tài khoản).
  Khóa ký: `AUTH_SECRET` hoặc tự sinh vào volume `app_secrets` (mode 600).
- **Sở hữu & chia sẻ** (migration 0005, 0006): `documents.owner_id`, `is_shared`; tài liệu không chủ luôn là tài liệu chung.
  Người dùng thấy tài liệu của mình + tài liệu chung; admin quản lý thêm tài liệu chung nhưng không xem tài liệu riêng
  của người khác. Truy cập trái phép → 404. Trùng SHA-256 tính theo chủ sở hữu. Chat/truy xuất chỉ tìm trong
  tài liệu được xem (danh sách rỗng → không tìm gì); hội thoại theo người dùng. `PATCH /api/documents/{id}` chia sẻ/bỏ chia sẻ.
- **Giới hạn**: `MAX_UPLOAD_MB=100`, `MAX_PDF_PAGES=300` (từ benchmark), loại file theo phần mở rộng; rate limit Redis
  theo người dùng (đăng nhập theo IP + tên, chat, upload; fail-open) + nginx `limit_req` theo IP; nginx `client_max_body_size 105m`.
- **Log JSON** một dòng/sự kiện với `request_id` (nhận/đặt header `X-Request-ID`), access log có method/path/status/
  thời gian/user_id; không ghi nội dung tài liệu, câu hỏi, mật khẩu. 4xx ghi warning, 5xx ghi vị trí stack.
- **Health**: `/health` (liveness), `/health/ready` (PostgreSQL, S3, Chroma, Redis; 503 nếu lỗi; healthcheck của image),
  `/health/llm` (không còn lộ base URL); `/api/system/status` (đăng nhập): thành phần, LLM, worker, hàng đợi, số tài liệu/job, cấu hình.
- **Frontend**: trang đăng nhập, menu tài khoản (trạng thái hệ thống, quản lý người dùng cho admin, đổi mật khẩu, đăng xuất),
  nút chia sẻ + nhãn "Chung" trong thư viện, ẩn thao tác khi không có quyền; 401 ở bất kỳ đâu → về trang đăng nhập.
  Script theme đưa ra file để CSP cấm script inline. `pdfjs-dist` 5.7 → 6.3.289 (npm audit: lỗ hổng high GHSA-hq66-cqwq-w95j
  "thực thi JS khi mở PDF độc hại"; bản vá cùng thư viện).
- **nginx**: CSP, X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy, `server_tokens off`, `X-Request-ID`.
  `FRONTEND_BIND`/`FRONTEND_PORT` để mở cho mạng nội bộ khi cần (mặc định 127.0.0.1:8080).
- Backup/restore dừng cả api và worker. README viết lại (yêu cầu máy, Ollama, .env, khởi động, tài khoản, backup/restore,
  chuyển LLM, triển khai, bảo mật, sự cố). Báo cáo cũ ở gốc chuyển vào `docs/history/`.
- Kiểm thử: backend **175 test** (27 test auth/phân quyền/rate limit/health/log/init/migration mới), frontend 35 test;
  E2E thật qua nginx **28/28**: header bảo mật, 401, health, đăng nhập, upload riêng tư, cô lập tài liệu/hội thoại/job,
  chat không thấy tài liệu riêng của người khác, chia sẻ, người khác không xóa/bỏ chia sẻ được (403), admin không tự đặt lại
  mật khẩu, file không cache, rate limit đăng nhập, khóa tài khoản thu hồi phiên, đăng xuất.

### Rà soát bảo mật (workflow nhiều agent + tự kiểm chứng)

4 hướng rà soát song song (phân quyền/phạm vi dữ liệu, xác thực/phiên, input/hạ tầng, correctness), mỗi phát hiện có agent
phản biện. Subagent hết hạn mức giữa chừng: hướng input/hạ tầng và correctness do tôi tự rà (thêm `alembic check`:
không lệch model/migration; downgrade/upgrade 0004↔0006 chạy được). Đã sửa (kèm test):

| Phát hiện | Mức | Sửa |
|---|---|---|
| Tài liệu không chủ (tạo bởi `migrate_legacy` sau 0005) thành riêng tư, không ai thấy | high | Migration 0006: ownerless ⇒ shared (CHECK constraint + default trong model + `visible_to`); FK chủ sở hữu RESTRICT (xóa user không biến tài liệu riêng thành chung) |
| Giới hạn đăng nhập theo tên cho phép khóa tài khoản admin từ máy khác | medium | Chỉ đếm lần **sai** theo (tên, IP), chặn trước khi kiểm mật khẩu; mọi lần thử vẫn giới hạn theo IP |
| Admin tự đặt lại mật khẩu mình qua `/api/users` không cần mật khẩu cũ | medium | 409 `use_password_change`; nút bị vô hiệu trên dòng của chính mình |
| `/api/auth/password` đoán mật khẩu hiện tại không giới hạn | low | Rate limit theo người dùng |
| `create_user` không `--admin` âm thầm hạ quyền admin | low | Giữ vai trò trừ khi có `--admin`/`--user` |
| Username unique phân biệt hoa/thường, tra cứu thì không | low | Lưu chữ thường + unique index `lower(username)` |
| Hai admin hạ quyền nhau cùng lúc → không còn admin | low | Khóa hàng (`FOR UPDATE`), luôn còn ≥ 1 admin hoạt động |
| `INITIAL_ADMIN_*` sai làm init crash cả stack | low | Ghi log lỗi, stack vẫn chạy |
| `/api/system/status` lộ số tài liệu/job riêng của người khác | low | Người dùng thường chỉ thấy số của tài liệu mình được xem |
| SSE tiến độ job chỉ kiểm quyền lúc kết nối | low | Kiểm lại phiên + quyền xem mỗi lần poll |
| File gốc được cache 5 phút trong trình duyệt | low | `Cache-Control: no-store` |
| (tự rà) alembic ghi đè logging JSON của `init` | low | `configure_logger=False` khi gọi từ init |

Rủi ro chấp nhận (ghi trong README "Bảo mật"): admin đặt lại mật khẩu người khác thì đăng nhập thay được (đã ghi log
`changed=password`); đăng xuất không thu hồi token phía server (token nằm trong cookie HttpOnly, hết hạn sau `SESSION_HOURS`);
`X-Real-IP` được tin khi gọi thẳng cổng API (chỉ mở trên 127.0.0.1); trích dẫn cũ vẫn nằm trong lịch sử hội thoại
sau khi tài liệu bị bỏ chia sẻ (ảnh chụp tại thời điểm trả lời); log trong lúc stream SSE không mang `request_id`.

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

- `.env` của người dùng (không được sửa) còn cấu hình cũ: MiniLM / CHUNK 700/120 / TOP_K 4 / `MAX_UPLOAD_MB=25`, biến
  `OLLAMA_*`/`GEMINI_*` thừa; chưa có `INITIAL_ADMIN_*`. Cần đổi theo `.env.example` rồi
  `docker compose stop worker; docker compose run --rm api python -m scripts.reindex --drop-other-collections; docker compose start worker`.
- Tài khoản đã tạo trên máy dev: `admin` (mật khẩu ngẫu nhiên đã báo người dùng — nên đổi ngay), `e2e_user`, `e2e_other` (đã khóa, chỉ dùng kiểm thử).
- Bộ 30–50 câu hỏi riêng của người dùng: chưa có; chạy theo mẫu `evaluation/custom_questions.example.json`.
- Hiệu năng: LLM là nút thắt khi nhiều người hỏi (Ollama tuần tự, 7B không vừa GPU 4 GB); embedding CPU ~1,2 s/trang.
  Hướng: GPU ≥ 8 GB hoặc server LLM riêng + `OLLAMA_NUM_PARALLEL`; torch CUDA cho worker khi có GPU riêng.
- Chưa có: HTTPS (đặt sau reverse proxy + `COOKIE_SECURE=true`), đăng xuất mọi phiên (ngoài đổi mật khẩu), audit log riêng,
  quét lỗ hổng Python (pip-audit chưa cài — cần hỏi trước khi thêm công cụ).
