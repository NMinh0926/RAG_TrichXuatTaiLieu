# Trợ lý tài liệu nội bộ (RAG tiếng Việt)

Web app hỏi đáp trên tài liệu nội bộ: tải lên PDF (có text hoặc scan), DOCX, Excel, ảnh → hệ thống
đọc/OCR, chia đoạn, tạo vector → người dùng hỏi bằng tiếng Việt, câu trả lời **chỉ dựa trên tài liệu**
và có trích dẫn `[n]`; bấm trích dẫn để mở đúng trang PDF với đoạn nguồn được tô sáng.

- Đăng nhập bằng tài khoản do quản trị viên tạo; mỗi người thấy tài liệu của mình và tài liệu được chia sẻ chung.
- LLM gọi qua API tương thích OpenAI (`/v1/chat/completions`). Mặc định: **Ollama chạy trên máy host**
  (`qwen2.5:7b`), tài liệu không rời khỏi máy. Không dùng dịch vụ AI của Google.
- Toàn bộ chạy bằng một lệnh `docker compose up -d`; dữ liệu nằm trong Docker volume.

Tài liệu khác: [docs/PROGRESS.md](docs/PROGRESS.md) (lịch sử các giai đoạn, quyết định),
[docs/benchmarks/report.md](docs/benchmarks/report.md) (hiệu năng, giới hạn upload, cấu hình máy),
[CLAUDE.md](CLAUDE.md) (lệnh cho người phát triển).

## Kiến trúc

```text
Trình duyệt ──► frontend (nginx: giao diện React + reverse proxy /api, giới hạn request, security headers)
                   │
                   ▼
                 api (FastAPI) ── đăng nhập, tài liệu, chat SSE, trạng thái hệ thống
                   │   ├─► PostgreSQL   metadata: người dùng, tài liệu, chunk + vị trí, job, hội thoại
                   │   ├─► SeaweedFS    file gốc (API S3; đổi sang S3/R2 chỉ bằng cấu hình)
                   │   ├─► Chroma       vector (mỗi vector gắn document_id trong PostgreSQL)
                   │   ├─► Redis        hàng đợi job nạp tài liệu + giới hạn tần suất
                   │   └─► LLM          Ollama trên host (hoặc bất kỳ API tương thích OpenAI)
                 worker (RQ) ── tải file → text layer / OCR Tesseract song song → chunk → embedding bge-m3 → Chroma
                 init ── migration DB, kiểm tra storage, tạo khóa ký phiên, tạo admin đầu tiên
```

Luồng hỏi đáp: viết lại câu hỏi nối tiếp theo lịch sử → phân loại (hỏi đáp / tóm tắt) →
lấy Top-K đoạn liên quan **trong phạm vi tài liệu người dùng được xem** (không chặn bằng ngưỡng điểm) →
LLM trả lời có trích dẫn hoặc nói "Tài liệu không có đủ thông tin…". Tóm tắt toàn tài liệu dùng map-reduce
và được cache.

## Yêu cầu máy

Số đo chi tiết: [docs/benchmarks/report.md](docs/benchmarks/report.md).

| | Tối thiểu | Khuyến nghị (máy đã đo) |
|---|---|---|
| CPU | 4 nhân / 8 luồng x86-64 | 8+ nhân (đã đo: Core i5-12500H, 16 luồng) |
| RAM | 16 GB (Docker ≥ 6 GB + Ollama `qwen2.5:3b`) | 16–32 GB (Docker 8 GB + Ollama `qwen2.5:7b`) |
| GPU | Không bắt buộc (LLM trên CPU chậm, ~vài token/giây) | NVIDIA ≥ 4 GB VRAM cho LLM 7B lượng tử hóa |
| Ổ đĩa | 20 GB trống (image ~2 GB, model ~8 GB) + dữ liệu | SSD; dữ liệu sinh ra ≈ 1,05–1,4 × dung lượng file tải lên |
| Phần mềm | Windows 10/11 hoặc Linux, Docker Desktop / Docker Engine + Compose v2, Ollama ≥ 0.5 | |

## Cài đặt

### 1. Ollama (trên máy host)

1. Tải và cài từ <https://ollama.com/download> (chạy nền, cổng 11434).
2. Đặt ngữ cảnh 8192 token cho Ollama (Windows PowerShell), rồi **khởi động lại Ollama**:
   ```powershell
   [Environment]::SetEnvironmentVariable('OLLAMA_CONTEXT_LENGTH', '8192', 'User')
   ```
3. Tải model:
   ```powershell
   ollama pull qwen2.5:7b     # trả lời (≈ 4,7 GB)
   ollama pull qwen2.5:3b     # khuyến nghị cho bước tóm tắt từng phần (LLM_SUMMARY_MODEL), máy yếu dùng làm model chính
   ollama list
   ```

Container gọi Ollama qua `http://host.docker.internal:11434/v1` (đã ánh xạ trong `compose.yaml`).

### 2. Cấu hình `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

Bắt buộc sửa trước khi dùng chung:

| Biến | Ý nghĩa |
|---|---|
| `POSTGRES_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Mật khẩu nội bộ giữa các container; đặt giá trị ngẫu nhiên, dài. |
| `INITIAL_ADMIN_USERNAME`, `INITIAL_ADMIN_PASSWORD` | Tài khoản admin đầu tiên, chỉ được tạo khi chưa có tài khoản nào. Xóa mật khẩu khỏi `.env` sau lần chạy đầu. |
| `AUTH_SECRET` | Khóa ký phiên đăng nhập (≥ 32 ký tự). Để trống: service `init` tự sinh và lưu trong volume `app_secrets`. |
| `FRONTEND_BIND` | `127.0.0.1` (chỉ máy này) hoặc `0.0.0.0` (cho máy khác trong mạng nội bộ truy cập). |

Các biến khác (model, chunk, giới hạn upload, rate limit, log) có chú thích trong `.env.example`.
`.env` không bao giờ được commit.

### 3. Khởi động

```powershell
docker compose up -d --build     # lần đầu: build image, tải model embedding (~2,3 GB) khi worker khởi động
docker compose ps                # mọi service "healthy"; init "exited (0)"
```

Mở <http://localhost:8080> và đăng nhập. Kiểm tra nhanh:

```powershell
curl.exe http://localhost:8080/health/ready    # PostgreSQL, S3, Chroma, Redis
curl.exe http://localhost:8080/health/llm      # Ollama + model
```

Trong giao diện: menu tài khoản → **Trạng thái hệ thống** (mọi thành phần, worker, hàng đợi, cấu hình).

Dừng: `docker compose down` (dữ liệu giữ nguyên trong volume). **Không** dùng `docker compose down -v`
(xóa toàn bộ dữ liệu).

### Tài khoản

- Tạo admin/người dùng bằng lệnh (hỏi mật khẩu, không hiện trên màn hình):
  ```powershell
  docker compose run --rm -it api python -m scripts.create_user <tên> [--admin | --user]
  ```
  Chạy lại với tên đã có = đặt lại mật khẩu (và mở khóa), giữ nguyên vai trò nếu không truyền `--admin`/`--user`.
  Tên đăng nhập không phân biệt hoa/thường.
- Admin quản lý người dùng trong giao diện: menu tài khoản → **Quản lý người dùng** (tạo, đặt lại mật khẩu,
  đổi vai trò, khóa). Khóa tài khoản hoặc đổi mật khẩu sẽ đăng xuất mọi phiên của người đó.
- Tài liệu mới tải lên là **riêng tư**; chủ sở hữu bấm biểu tượng chia sẻ để mọi người cùng xem và hỏi đáp.
  Admin quản lý (xóa, bỏ chia sẻ) được các tài liệu chung. Tài liệu có từ trước khi có tài khoản là tài liệu chung.

## Sao lưu và khôi phục

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1                          # → backups\<thời điểm>\
powershell -ExecutionPolicy Bypass -File scripts\restore.ps1 -From backups\<thời điểm>
```

Bản sao lưu gồm: dump PostgreSQL, mọi file gốc (qua API S3), volume Chroma. API và worker được dừng trong
lúc sao lưu để ba phần nhất quán, rồi tự khởi động lại. Nên chép thư mục `backups\` sang ổ khác/máy khác.
Khóa ký phiên không cần sao lưu (mất khóa chỉ làm mọi người phải đăng nhập lại).

## Chuyển LLM sang server khác / API tương thích OpenAI

Ứng dụng chỉ dùng `/v1/chat/completions` và `/v1/models`, nên đổi nhà cung cấp chỉ bằng `.env`:

| Trường hợp | Cấu hình |
|---|---|
| Ollama trên máy khác trong mạng | `LLM_BASE_URL=http://<ip-may-gpu>:11434/v1` (trên máy đó đặt `OLLAMA_HOST=0.0.0.0`) |
| vLLM / LM Studio / llama.cpp server | `LLM_BASE_URL=http://<host>:<port>/v1`, `LLM_MODEL=<tên model server báo>` |
| API trả phí tương thích OpenAI | `LLM_BASE_URL=https://<nhà cung cấp>/v1`, `LLM_API_KEY=<khóa>`, `LLM_MODEL=<model>` |

Sau đó chỉnh `LLM_CONTEXT_TOKENS` theo ngữ cảnh thật của model và chạy
`docker compose up -d --force-recreate api worker`, kiểm tra `curl.exe http://localhost:8080/health/llm`.
Lưu ý: dùng API bên ngoài nghĩa là đoạn trích tài liệu được gửi ra khỏi máy — cần được phép trước.

Đổi model **embedding** hoặc kích thước chunk thì phải lập chỉ mục lại toàn bộ:
`docker compose stop worker; docker compose run --rm api python -m scripts.reindex --drop-other-collections; docker compose start worker`.

## Triển khai sau này

- Đặt sau reverse proxy có HTTPS, rồi `COOKIE_SECURE=true`.
- File gốc lên S3/R2: đặt `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_REGION`; chép dữ liệu cũ bằng
  `python -m scripts.objects_transfer`.
- PostgreSQL/Redis/Chroma được quản lý: đổi `POSTGRES_HOST`/`POSTGRES_PORT` (cùng `POSTGRES_USER/PASSWORD/DB`),
  `REDIS_URL`, `CHROMA_HOST/PORT` trong `.env`. `DATABASE_URL` chỉ dùng khi chạy backend ngoài Docker; các container bỏ qua nó.
- Nhiều worker: `docker compose up -d --scale worker=2` (mỗi worker giữ một bản model embedding ~2 GB RAM).
- Log dạng JSON một dòng (`LOG_FORMAT=json`), mỗi request có `request_id` (cũng trả về ở header `X-Request-ID`).

## Bảo mật (đã rà soát ở Giai đoạn 6)

- Giới hạn đăng nhập tính số lần **sai** theo (tên đăng nhập, IP) nên không ai khóa được tài khoản người khác từ máy mình.
- Mật khẩu băm scrypt; phiên là token ký HMAC trong cookie `HttpOnly; SameSite=Strict` (hoặc header
  `Authorization: Bearer` cho script), hết hạn sau `SESSION_HOURS`; đổi mật khẩu/khóa tài khoản thu hồi mọi token.
- Phân quyền kiểm tra ở mọi endpoint và ở bước truy xuất vector: tài liệu không được xem thì không bao giờ
  lọt vào ngữ cảnh LLM; truy cập tài liệu của người khác trả 404.
- Giới hạn: kích thước và số trang upload, loại file theo phần mở rộng, độ dài câu hỏi, số request (nginx theo IP,
  Redis theo người dùng: đăng nhập, chat, upload).
- Secret chỉ qua biến môi trường / `.env`; log không ghi nội dung tài liệu, câu hỏi hay mật khẩu; lỗi 500 không trả chi tiết.
- Chỉ nginx được publish (mặc định chỉ trên localhost); PostgreSQL, Redis, Chroma, SeaweedFS không mở cổng ra ngoài.
  Cổng API 8000 chỉ mở trên `127.0.0.1` cho quản trị/tài liệu OpenAPI (`/docs`).
- Content-Security-Policy chặn script inline/bên ngoài; markdown câu trả lời không render HTML thô.
- Rủi ro đã biết và chấp nhận cho môi trường nội bộ:
  - Admin đặt lại mật khẩu của người khác thì có thể đăng nhập thay họ (mọi thay đổi tài khoản đều ghi log, kèm trường đã đổi).
  - Đăng xuất xóa cookie nhưng không thu hồi token phía server; token hết hạn sau `SESSION_HOURS`. Muốn chấm dứt mọi phiên: đổi mật khẩu.
  - Gọi thẳng cổng API 8000 (chỉ mở trên máy chủ) có thể giả `X-Real-IP` để né giới hạn theo IP.
  - Trích dẫn trong lịch sử hội thoại là bản chụp lúc trả lời, vẫn còn sau khi tài liệu bị bỏ chia sẻ hoặc xóa.

## Phát triển

Lệnh test, benchmark, migration, đánh giá: xem [CLAUDE.md](CLAUDE.md). Tóm tắt:

```powershell
docker compose run --rm -v "${PWD}:/app" api python -m pytest -q -p no:cacheprovider          # backend
docker run --rm -v "${PWD}/frontend:/workspace" -w /workspace node:22-alpine sh -c "npm ci && npm run typecheck && npm run test && npm run build"
```

Bộ câu hỏi đánh giá riêng (30–50 câu có đáp án): chép `evaluation/custom_questions.example.json` thành
`evaluation/custom/questions.json`, đặt tài liệu vào `evaluation/custom/` (không commit), chạy
`docker compose run --rm -v "${PWD}:/app" api python -m scripts.evaluate --extra-cases evaluation/custom/questions.json`.

## Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| `/health/llm` báo `llm_unavailable` | Ollama chưa chạy trên host: mở Ollama, `ollama list`. |
| `llm_model_missing` | `ollama pull <LLM_MODEL>`. |
| Tài liệu đứng ở "Đang chờ" | Trạng thái hệ thống → Worker; `docker compose logs --tail=100 worker`. |
| Không đăng nhập được lần đầu | Chưa có tài khoản: đặt `INITIAL_ADMIN_*` rồi `docker compose up -d`, hoặc chạy `scripts.create_user`. |
| Upload bị từ chối 413 | File vượt `MAX_UPLOAD_MB` hoặc PDF vượt `MAX_PDF_PAGES` — tách file. |
| Trả lời rất chậm | LLM đang chạy CPU hoặc nhiều người hỏi cùng lúc (Ollama xử lý tuần tự); xem báo cáo benchmark. |
