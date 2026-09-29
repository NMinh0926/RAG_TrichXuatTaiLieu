# CLAUDE.md

## Dự án
Hệ thống RAG hỏi đáp tài liệu tiếng Việt.
Luồng hiện tại: upload PDF → đọc text / OCR (PaddleOCR) nếu trang không có text → chunk → embedding (sentence-transformers) → ChromaDB → LLM sinh câu trả lời có trích dẫn nguồn.

## Mục tiêu
Nâng cấp từ bản demo thành web app hoàn thiện, thân thiện với người dùng, dùng NỘI BỘ trước.
Chưa deploy public, nhưng kiến trúc phải sẵn sàng để deploy sau này mà không phải viết lại.
Cụ thể: câu trả lời chi tiết hơn, xử lý nhanh hơn, giao diện đẹp, xem lại file đã nạp,
bấm trích dẫn để mở file và highlight đúng chỗ, có benchmark.
File và dữ liệu lưu trong database/storage thay vì thư mục `./data/uploads` trên máy.

## Lưu trữ (mục tiêu)
- File gốc: MinIO (object storage chuẩn S3) chạy trong docker compose. Sau này đổi sang
  S3/Cloudflare R2 chỉ bằng cấu hình.
- Metadata (tài liệu, trang, chunk, job xử lý, lịch sử chat): PostgreSQL.
- Vector: ChromaDB (chế độ server hoặc persistent volume), mỗi vector liên kết `document_id` trong PostgreSQL.
- Toàn bộ chạy được bằng một lệnh `docker compose up`, dữ liệu nằm trong Docker volume.

## Quyết định đã chốt (không thay đổi nếu tôi chưa đồng ý)
- KHÔNG dùng Gemini hay bất kỳ dịch vụ Google AI nào.
- LLM gọi qua API tương thích OpenAI (`/v1/chat/completions`), cấu hình bằng
  `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`. Môi trường dev dùng Ollama
  (`qwen2.5:7b`) chạy trên máy host Windows, app chạy trong Docker và gọi qua
  `host.docker.internal`.
- Retrieval KHÔNG chặn bằng ngưỡng cứng (đã đo: ngưỡng 0.55 làm mất 6/10 câu có đáp án).
  Dùng Top-K (+ rerank khi có), để LLM quyết định ngữ cảnh có đủ hay không.
- Mọi câu trả lời chỉ dựa trên tài liệu, có trích dẫn nguồn (file, trang).

## Quy tắc làm việc
- Làm theo từng giai đoạn. Đầu mỗi giai đoạn: đọc code liên quan, lập kế hoạch,
  CHỜ tôi duyệt rồi mới sửa.
- Không cài dependency mới hoặc đổi công nghệ lớn (framework, database, vector store,
  model) khi chưa hỏi tôi. Khi đề xuất, nêu ngắn gọn ưu/nhược và lựa chọn thay thế.
- Commit sau mỗi bước nhỏ đã chạy được, message tiếng Anh theo Conventional Commits.
- Viết/cập nhật test cho mọi phần mới; chạy toàn bộ test trước khi báo xong.
  Test không gọi LLM hay mạng thật (dùng mock).
- Không đọc, sửa hay commit `.env`. Secret chỉ đi qua biến môi trường; cập nhật `.env.example`.
- Thay đổi liên quan hiệu năng: đo trước và sau, lưu kết quả vào `docs/benchmarks/`.
- Giữ API mà frontend đang dùng tương thích ngược, trừ khi giai đoạn đó thay frontend.
- Code có type hints, theo style sẵn có của project, không để lại code chết.
- Cuối mỗi giai đoạn: cập nhật `docs/PROGRESS.md` (đã làm gì, quyết định gì,
  việc còn dở, số liệu đo) để có thể `/clear` rồi làm tiếp mà không mất ngữ cảnh.
- Trả lời tôi bằng tiếng Việt; code, comment và commit bằng tiếng Anh.

## Lệnh thường dùng
Chạy từ thư mục gốc project bằng PowerShell. Python chỉ chạy trong Docker (host không có Python).
- Chạy app:
  - Ollama trên host phải đang chạy: `ollama list` (cần `qwen2.5:7b`, khuyến nghị thêm `qwen2.5:3b`),
    biến môi trường user `OLLAMA_CONTEXT_LENGTH=8192`.
  - Toàn bộ (postgres, seaweedfs, chroma, redis, init, api, worker, frontend): `docker compose up -d --build`
    → giao diện http://localhost:8080 ; API http://127.0.0.1:8000/docs ;
    kiểm tra `curl.exe http://127.0.0.1:8000/health` và `curl.exe http://127.0.0.1:8000/health/llm`.
  - Frontend dev (hot reload): `cd frontend; npm run dev` (proxy tới :8000) hoặc qua node:22-alpine.
- Chạy test (dùng source hiện tại, không cần build lại image):
  - Toàn bộ: `docker compose run --rm -v "${PWD}:/app" api python -m pytest -q -p no:cacheprovider`
  - Nhanh (bỏ OCR/embedding thật): thêm `-m "not integration"`.
  - Frontend: `docker run --rm -v "${PWD}/frontend:/workspace" -w /workspace node:22-alpine sh -c "npm ci && npm run typecheck && npm run test && npm run build"`
- Xem log: `docker compose logs -f --tail=100 api worker`
- Migration DB: tự chạy bởi service `init`; tạo migration mới:
  `docker compose run --rm --no-deps -v "${PWD}:/app" api alembic revision --autogenerate -m "<msg>"`
- Chuyển dữ liệu cũ `./data`: `docker compose run --rm -v "${PWD}/data:/legacy:ro" api python -m scripts.migrate_legacy --uploads /legacy/uploads --chroma /legacy/chroma`
- Backup / restore: `powershell -ExecutionPolicy Bypass -File scripts\backup.ps1` ;
  `powershell -ExecutionPolicy Bypass -File scripts\restore.ps1 -From backups\<timestamp>`
- Benchmark (DB `<db>_scratch`, bucket `-scratch`, Chroma local; không đụng dữ liệu thật, job chạy inline):
  - `docker compose run --rm -v "${PWD}:/app" api python -m scripts.benchmark.make_test_pdf`
  - `docker compose run --rm -v "${PWD}:/app" api python -m scripts.benchmark.bench_ingest`
  - `docker compose run --rm -v "${PWD}:/app" api python -m scripts.benchmark.bench_qa`
- Kiểm tra LLM: `curl.exe http://127.0.0.1:8000/health/llm` ; `docker compose run --rm api python -m scripts.check_llm`
- Re-index toàn bộ (sau khi đổi embedding/chunk/OCR): `docker compose stop worker` rồi
  `docker compose run --rm api python -m scripts.reindex --drop-other-collections` rồi `docker compose start worker`
- A/B retrieval: `docker compose run --rm --no-deps -v "${PWD}:/app" api python -m scripts.ab_retrieval`
- Evaluation retrieval: `docker compose run --rm -v "${PWD}:/app" api python -m scripts.evaluate --retrieval-only`
