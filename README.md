# Multi-modal RAG — Trợ lý truy xuất tài liệu

> Sinh câu trả lời bằng **Ollama chạy local** (mặc định `qwen2.5:7b`); không dùng API LLM đám mây.
> `/api/questions/ask` gửi raw Top-K cho LLM để LLM tự quyết định tài liệu có đủ thông tin không
> (xem [REPORT_RETRIEVAL_GATING.md](REPORT_RETRIEVAL_GATING.md)). Ollama lỗi → HTTP 5xx, không phải no-answer.

> A/B embedding local: [REPORT_EMBEDDING_AB.md](REPORT_EMBEDDING_AB.md).
> Đã thử BGE-M3 và E5-base trong DB riêng, giữ cùng chunks/dataset. Chưa đổi
> model hoặc threshold production; kết quả calibration không thay thế kiểm chứng thực tế.

Backend cho bài đánh giá AI Intern: đọc tài liệu nội bộ đa định dạng, trích chữ/OCR,
lưu vector và trả lời dựa trên ngữ cảnh đã truy xuất. Frontend React có hai trang nạp
tài liệu và hỏi đáp; không có chat history.
Đây là bản phục vụ bài đánh giá, chưa được đánh giá cho môi trường production.

## Bài toán và định dạng

PDF có text, PDF scan, DOCX (paragraph + table), XLS/XLSX (từng sheet), PNG/JPG/JPEG.
Mục tiêu: câu trả lời có căn cứ và metadata nguồn; không có thông tin thì từ chối trả lời.
Mẫu và evaluation corpus trong repository đều là dữ liệu tổng hợp.

## Kiến trúc và vai trò LLM

```text
PDF / DOCX / Excel / Image / Scanned PDF
  → Extract / PaddleOCR → DocumentUnit → Clean → token chunks + overlap
  → SentenceTransformer → ChromaDB PersistentClient

Question → cùng SentenceTransformer → raw Top-K candidates (không lọc 0.55)
  ├─ không có candidate (collection rỗng) → "Tôi không tìm thấy thông tin này trong tài liệu đã tải lên."
  └─ có candidates → Ollama POST /api/chat (stream=false)
       ├─ trả lời + [Nguồn: file, trang N] → answer + sources của các chunk được trích
       ├─ "Tài liệu không có đủ thông tin để trả lời câu hỏi này." → sources rỗng
       └─ Ollama lỗi (không chạy / chưa pull model / timeout) → HTTP 5xx, KHÔNG phải no-answer
```

Relevant và no-answer có cosine score chồng lấn (relevant thấp nhất 0.234, no-answer
cao nhất 0.533 trên 39 câu đo), nên một ngưỡng similarity cố định không thể quyết định
“tài liệu có hỗ trợ câu trả lời không”. Threshold chỉ còn dùng cho `/questions/search`.

**LLM chỉ dùng cho answer generation**, chạy local qua Ollama; dữ liệu không rời máy.
LLM nhận system prompt (`app/services/prompts.py`) và user message dạng:

```text
NGỮ CẢNH:
[1] (<tên file>, trang <số trang>) <nội dung chunk>
[2] ...
CÂU HỎI: <câu hỏi>
```

PDF có số trang; Excel dùng `sheet <tên sheet>`; DOCX và ảnh không có metadata trang nên
chỉ ghi tên file. Nguồn trong response lấy từ metadata retrieval của các chunk mà câu trả lời
trích `[Nguồn: ...]` (so khớp tên file và trang/sheet); không trích hợp lệ thì `sources` rỗng.
Prompt giảm rủi ro suy diễn; không bảo đảm tuyệt đối mô hình không hallucinate.

## Tech stack

Python 3.11, FastAPI, PyMuPDF, python-docx, pandas/openpyxl/xlrd, Pillow, PaddleOCR 3.2,
sentence-transformers, ChromaDB PersistentClient, httpx (gọi Ollama HTTP API), pytest.
LLM: [Ollama](https://ollama.com) trên máy host, model mặc định `qwen2.5:7b`.

## Cấu trúc

```text
app/main.py                 FastAPI + error handlers + /health, /health/ollama
frontend/                   React + Vite: /documents và /ask
app/api/                    upload, ask và endpoint phase 1
app/core/                   config, dependencies, logging, errors
app/models/schemas.py       DocumentUnit, Chunk và API schemas
app/services/               loaders, OCR, cleaning, chunking, embedding,
                            vector_store, ingestion, retrieval, answering,
                            generation (Ollama), ollama_client, prompts
scripts/create_samples.py   mẫu cơ bản phase 1
scripts/create_evaluation_data.py
scripts/evaluate.py         đánh giá retrieval / Ollama thật
scripts/check_ollama.py     kiểm tra Ollama + model + hành vi grounded
tests/                      unit, integration, E2E (Ollama được mock)
sample_data/                mẫu cơ bản
evaluation/documents/       5 tài liệu được đánh giá + ảnh nguồn tạo scan
evaluation/questions.json  25 câu hỏi có kỳ vọng rõ ràng
data/uploads/               file nạp local, tên UUID
data/chroma/                vector persistent
```

## Cài Ollama trên Windows

1. Tải và cài từ https://ollama.com/download/windows (Ollama chạy nền, lắng nghe cổng 11434).
2. Mở PowerShell, tải model:

   ```powershell
   ollama pull qwen2.5:7b     # cần khoảng 5 GB RAM trống
   # Máy yếu: ollama pull qwen2.5:3b   rồi đặt OLLAMA_MODEL=qwen2.5:3b trong .env
   ollama list                # kiểm tra model đã có
   ```

3. Kiểm tra server: `curl.exe http://localhost:11434/api/tags`.

Ollama chạy **trên host**, không trong container. `compose.yaml` ánh xạ
`host.docker.internal:host-gateway` để container gọi được host qua
`OLLAMA_BASE_URL=http://host.docker.internal:11434`.

## Cài đặt bằng Docker

Cần Docker Desktop chạy Linux containers. Không cần Python/pip/venv trên host.

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 api
```

Dừng: `docker compose down`; khởi động lại: `docker compose up -d`.
Sau khi sửa mã hoặc requirements: `docker compose up -d --build`.
Sau khi sửa `.env`: `docker compose up -d --force-recreate`.

Dockerfile dùng Python 3.11, PyTorch CPU, dependencies khóa phiên bản và user không
phải root. `.env` chỉ nạp lúc chạy; `.dockerignore` không cho `.env`/data/cache vào image.
`data/` bind mount giữ uploads/Chroma. Volume `bai1-rag_model_cache` giữ model OCR và
embedding. Không dùng `docker compose down -v` nếu muốn giữ cache model.
Lần đầu cần mạng để tải image/model. PaddleOCR 3.2 có thể kiểm tra host dù có cache.

## Environment variables

Cấu hình trong `.env` (không commit). Không có API key/secret nào cho LLM.

| Biến | Mặc định/mẫu | Ý nghĩa |
|---|---|---|
| OLLAMA_BASE_URL | http://host.docker.internal:11434 | Địa chỉ Ollama; chạy backend ngoài Docker dùng http://localhost:11434 |
| OLLAMA_MODEL | qwen2.5:7b | Model đã `ollama pull`; máy yếu dùng qwen2.5:3b |
| OLLAMA_TIMEOUT_SECONDS | 120 | Timeout mỗi request (lần đầu model phải nạp vào RAM) |
| OLLAMA_TEMPERATURE | 0.2 | Temperature khi sinh câu trả lời |
| OLLAMA_NUM_CTX | 8192 | Context window (token) gửi cho Ollama |
| FRONTEND_ORIGINS | http://localhost:5173,http://127.0.0.1:5173 | CORS cho frontend local, không credentials |
| UPLOAD_DIR | ./data/uploads | Lưu file upload |
| CHROMA_PATH | ./data/chroma | Vector persistent |
| EMBEDDING_MODEL | sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | Embedding local |
| CHUNK_SIZE / CHUNK_OVERLAP | 700 / 120 | Kích thước trần và overlap mong muốn |
| TOP_K | 4 | Tối đa chunk trả về |
| RETRIEVAL_THRESHOLD | 0.55 | Chỉ cho `/questions/search` (legacy); không quyết định no-answer của `/api/questions/ask` |
| CANDIDATE_MIN_RELEVANCE | -1 (tắt) | Sàn loại nhiễu cho candidates gửi LLM; đo trên 39 câu không có sàn nào có lợi |
| OCR_LANG | vi | Ngôn ngữ PaddleOCR |
| PDF_MIN_TEXT_CHARS | 20 | Heuristic chuyển trang PDF sang OCR |
| MAX_UPLOAD_MB | 25 | Giới hạn file upload |
| HF_HOME / PADDLE_PDX_CACHE_HOME / PADDLE_HOME | ./.cache/... | Cache model local |

Đổi `.env` cần `docker compose up -d --force-recreate` để nạp lại biến môi trường.
Compose đặt đường dẫn uploads/Chroma dưới `/app/data` và cache dưới `/app/.cache`,
tương ứng bind mount và volume đã khai báo. Upload/retrieval không cần Ollama.

## Chạy backend

```powershell
docker compose up -d
```

Swagger: http://127.0.0.1:8000/docs. Chỉ mở cổng trên localhost.

- `GET /health`: liveness của API, không gọi Ollama.
- `GET /health/ollama`: gọi `GET {OLLAMA_BASE_URL}/api/tags` và xác nhận `OLLAMA_MODEL` đã được pull.
  200 → `{"status":"ok","model":"qwen2.5:7b","model_available":true,...}`;
  503 → `ollama_unavailable` (Ollama không chạy) hoặc `ollama_model_missing` (chưa pull).

```powershell
curl.exe http://127.0.0.1:8000/health/ollama
```

## Upload API

`POST /api/documents/upload`, multipart field `file`.

```json
{"status":"success","filename":"sample.docx","chunks_created":1}
```

Lưu file → ingestion → extraction/OCR → cleaning/chunking → embedding → Chroma.
File lỗi được dọn; tên gốc đã loại đường dẫn được giữ trong metadata.

## Ask API

`POST /api/questions/ask`, JSON `question`, tùy chọn `top_k` (1–100).
Question trắng/rỗng bị từ chối 422.

```json
{"question":"Một năm người lao động có bao nhiêu ngày phép?"}
```

Ví dụ hình dạng response (minh họa):

```json
{"answer":"Nhân viên được hưởng 12 ngày nghỉ phép mỗi năm [Nguồn: sample.docx].","sources":[{"filename":"sample.docx"}]}
```

Tài liệu không đủ thông tin (LLM quyết định) / chưa có tài liệu nào trong DB:

```json
{"answer":"Tài liệu không có đủ thông tin để trả lời câu hỏi này.","sources":[]}
{"answer":"Tôi không tìm thấy thông tin này trong tài liệu đã tải lên.","sources":[]}
```

Endpoint phase 1 vẫn giữ: `POST /documents/ingest`, `POST /questions/search`.
Không lưu câu hỏi/câu trả lời hoặc hội thoại.

## Năm ví dụ API request

Dùng các file đã có trong `sample_data/`:

```powershell
# 1. Upload PDF text
curl.exe -X POST http://127.0.0.1:8000/api/documents/upload -F "file=@sample_data/sample.pdf"
# 2. Upload DOCX
curl.exe -X POST http://127.0.0.1:8000/api/documents/upload -F "file=@sample_data/sample.docx"
# 3. Upload Excel (ảnh/scan dùng cùng endpoint)
curl.exe -X POST http://127.0.0.1:8000/api/documents/upload -F "file=@sample_data/sample.xlsx"
# 4. Ask câu có trong tài liệu
$body = @{question='Một năm người lao động có bao nhiêu ngày phép?'} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/questions/ask -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
# 5. Ask ngoài phạm vi
$body = @{question='CEO của Microsoft là ai?'} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/questions/ask -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

## OCR và RAG pipeline

PDF đọc từng trang bằng PyMuPDF. Khi text có dưới 20 ký tự chữ/số mới render ảnh rồi
OCR; trang có text đủ dùng không OCR lại. Image đi qua Pillow và cùng OCRService.
DOCX giữ thứ tự paragraph/table trong body. Excel lấy hàng không rỗng đầu tiên làm
header, serialize từng row thành `Column: value`, tách riêng từng sheet.

Clean NFC/whitespace, không xóa số/ký hiệu. Chunk dùng tokenizer và lát cắt text gốc.
[Model embedding mặc định](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)
có max_seq_length=128: chunker giới hạn theo budget còn lại sau special tokens, giữ
 tỷ lệ overlap 120/700 (thường 126 token nội dung và 22 overlap). Không để mất đuôi
của chunk 700 token do model tự truncate.

Cosine `relevance=1-distance`, không phải xác suất.
ID dựa trên hash tên file + bytes và chunk index: nạp lại cùng file không tăng vector.
Collection tách theo model/cấu hình chunk; đổi cấu hình cần nạp lại. File có nội dung
mới được coi là tài liệu mới; không có CRUD/quản lý phiên bản.

## Error handling và logging

415 unsupported format; 413 quá kích thước; 422 invalid input/file hỏng/không có chữ;
503 upload/OCR/embedding/Chroma/retrieval. Lỗi Ollama trả thông báo tiếng Việt:

| Tình huống | HTTP | `error` | `detail` |
|---|---|---|---|
| Không kết nối được Ollama | 503 | `ollama_unavailable` | Không kết nối được Ollama tại {url}. Hãy kiểm tra Ollama đang chạy. |
| Model chưa pull (Ollama 404) | 503 | `ollama_model_missing` | Model {model} chưa được tải. Chạy: ollama pull {model} |
| Quá thời gian | 504 | `ollama_timeout` | Gợi ý tăng OLLAMA_TIMEOUT_SECONDS hoặc dùng model nhỏ hơn |
| Lỗi HTTP khác / nội dung rỗng/sai | 502 | `ollama_error`, `ollama_empty_response`, `ollama_invalid_response` | |

Frontend hiển thị `detail` của lỗi, không biến lỗi thành câu no-answer.
Server log chi tiết lỗi Ollama (URL, model, status, trích body lỗi), số context/top score,
LLM called/skipped; không log nội dung tài liệu hoặc câu hỏi đầy đủ. Không trả stack trace qua API.

## Tests trong Docker

```powershell
# Toàn bộ, gồm OCR/embedding/Chroma thật; Ollama luôn được mock (không gọi thật)
docker compose run --rm api python -m pytest -v -p no:cacheprovider
# Unit/API tests nhanh
docker compose run --rm api python -m pytest -v -m 'not integration' -p no:cacheprovider
```

HTTP tới Ollama được mock bằng `httpx.MockTransport` (`tests/test_generation.py`): trả lời
thành công, Ollama không chạy, model chưa pull, timeout, không có chunk nào vượt ngưỡng
nhưng vẫn tới LLM, không có candidate thì không gọi LLM. Kiểm tra Ollama thật dùng
`scripts.check_ollama`. Persistence được kiểm tra qua hai tiến trình riêng.

## Evaluation

25 câu: PDF 5, DOCX 4, Excel 4, image OCR 4, scanned PDF 4, no-answer 4.
Mỗi case có question, expected_keywords, source, page/sheet khi có.
Tài liệu tại `evaluation/documents/`; database evaluation tách biệt trong cache volume của Docker.

```powershell
# Dataset đã có trong evaluation/, không cần tạo lại.
# Retrieval thật, không gọi Ollama; answer/no-answer accuracy để null
docker compose run --rm --volume "${PWD}/evaluation:/app/evaluation" api python -m scripts.evaluate --retrieval-only
# Toàn pipeline với Ollama thật (Ollama phải đang chạy và đã pull model)
docker compose run --rm --volume "${PWD}/evaluation:/app/evaluation" api python -m scripts.evaluate
```

`retrieval_ranking`: Hit@1/Hit@3/Hit@4, MRR trên raw Top-K **trước threshold**, phân phối
score relevant vs no-answer và sweep candidate floor. `metrics.retrieval_hit_rate` là metric
cũ (sau threshold), chỉ để so sánh. `answering`: answer accuracy trên câu có đáp án và
unsupported accuracy trên câu no-answer, qua đúng luồng candidates → LLM của API.
`--extra-cases file.json` thêm tài liệu/câu hỏi local (ví dụ PDF người dùng) mà không sửa dataset.
Relevant chunk: đúng source/location và keyword trong cùng chunk. Mẫu số là 21 câu có đáp án,
loại 4 câu no-answer. Answer correctness dùng keyword + source, mẫu số 25; lỗi được tính
không đúng. No-answer accuracy tính riêng 4 câu no-answer (chấp nhận cả hai câu từ chối).
Đây là kiểm tra keyword đơn giản, không thay thế chấm bởi con người.
Không dùng LLM giả để tạo metrics. Kết quả từng câu được ghi JSON.

Kết quả retrieval: `evaluation/results_retrieval.json`. Các `REPORT*.md` là lịch sử (thời dùng Gemini).

## Known limitations

- Prompt grounding không bảo đảm tuyệt đối; model local nhỏ có thể bỏ trích nguồn hoặc diễn đạt
  sai câu từ chối — khi đó `sources` rỗng hoặc câu trả lời được hiển thị nguyên văn.
- Chất lượng/tốc độ phụ thuộc model và phần cứng; 7B trên CPU có thể mất hàng chục giây/câu.
- OCR phụ thuộc chất lượng scan; mẫu OCR đánh giá hiện là tiếng Anh rõ nét.
- Chưa đọc hiểu biểu đồ/hình ảnh, tái dựng layout bảng scan, OCR ảnh nhúng DOCX,
  header/footer Word hay tính công thức Excel. `.xls` có nhánh xlrd nhưng fixture là XLSX.
- DOCX và ảnh không có số trang trong metadata; nguồn chỉ ghi tên file.
- Câu hỏi rất dài có thể bị model embedding truncate; nên hỏi ngắn, một vấn đề mỗi lần.
- Chưa benchmark tải đồng thời. Tải model ban đầu tốn thời gian/dung lượng.

## Chuyển đổi và dọn dẹp

Xem `DOCKER_MIGRATION.md` cho kết quả kiểm chứng và danh sách đã dọn.
Báo cáo giai đoạn 1/2 là lịch sử; lệnh chạy hiện tại ở README này.

## Frontend

React + Vite, chỉ hai trang:

- `/documents`: chọn file, nạp tài liệu, hiển thị filename và `chunks_created`.
- `/ask`: gửi câu hỏi, hiển thị câu trả lời và nguồn filename/page/sheet, no-answer
  hoặc thông báo lỗi backend. Mỗi lần chỉ giữ một câu trả lời trong bộ nhớ trang.

Không có danh sách tài liệu persistent, CRUD hoặc lịch sử hội thoại.

## Run Frontend

Cần Node.js 22.12+ và npm. Trong terminal riêng:

```powershell
cd frontend
npm install
npm run dev
# Kiểm tra production build
npm run build
```

Mở http://localhost:5173/documents hoặc http://localhost:5173/ask.
Project không khai báo lint script. Lockfile npm được giữ để tái lập dependencies.

Máy chỉ có Docker có thể chạy frontend bằng image Node (không cần Dockerfile frontend):

```powershell
docker run --rm -v "${PWD}/frontend:/workspace" -w /workspace node:22-alpine npm ci
docker run --rm -p 127.0.0.1:5173:5173 -v "${PWD}/frontend:/workspace" -w /workspace node:22-alpine npm run dev
```

Chạy hai lệnh Docker trên từ thư mục gốc project. Trong phiên hoàn thiện đã dùng
container `bai1-rag-frontend-demo`; dừng bằng `docker stop bai1-rag-frontend-demo`,
chạy lại bằng `docker start bai1-rag-frontend-demo`.

## Frontend environment

`frontend/.env.example`:

```dotenv
VITE_API_URL=http://127.0.0.1:8000
```

Đây cũng là giá trị mặc định cho demo local. Khi cần đổi, tạo `frontend/.env` từ
file mẫu và sửa URL backend. Khởi động lại Vite sau khi đổi cấu hình; build lại nếu
dùng bản build. Đổi port/origin frontend cần cập nhật `FRONTEND_ORIGINS` ở backend.

## Chạy backend local không Docker

Docker vẫn là môi trường đã kiểm chứng trên máy này. Nếu có Python 3.11
(đặt `OLLAMA_BASE_URL=http://localhost:11434` trong `.env`):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.evaluate --retrieval-only
.\.venv\Scripts\python.exe -m scripts.check_ollama
```

## Ollama diagnostic

```powershell
docker compose run --rm api python -m scripts.check_ollama
```

Script chỉ dùng text tổng hợp (“Dự án Orion có mã nội bộ ZXQ-91827.”), không gửi tài liệu người dùng:

1. `GET /api/tags`, xác nhận `OLLAMA_MODEL` đã pull → `OLLAMA HEALTH PASS`.
2. Câu hỏi có trong ngữ cảnh → câu trả lời chứa ZXQ-91827.
3. Câu hỏi không có trong ngữ cảnh → đúng câu “Tài liệu không có đủ thông tin…”.
   Cả hai đạt → `OLLAMA GROUNDED BEHAVIOR PASS`, exit 0.

## Demo Flow

1. Bật Ollama trên Windows, `ollama pull qwen2.5:7b`.
2. Tại thư mục gốc: `docker compose up -d --build`; kiểm tra `/health` và `/health/ollama` trả `ok`.
3. Chạy frontend bằng lệnh ở trên, mở `http://localhost:5173/documents`.
4. Chọn `sample_data/sample.docx`, bấm **Nạp tài liệu**, xem số chunk thành công.
5. Chuyển sang **Hỏi tài liệu**, hỏi: `Một năm người lao động có bao nhiêu ngày phép?`
   → câu trả lời kèm nguồn `sample.docx`.
6. Hỏi `What is the distance from Earth to Mars?` → “Tài liệu không có đủ thông tin…”.
7. Tắt Ollama rồi hỏi lại → UI hiện lỗi “Không kết nối được Ollama…”, không phải no-answer.

## Security

- `.env` và `frontend/.env` được ignore, chỉ `.env.example` được chia sẻ.
- Không có API key LLM; Ollama chạy local, tài liệu không gửi ra ngoài máy.
- Frontend chỉ gọi FastAPI, không gọi Ollama trực tiếp.
- Nếu `.env` cũ còn `GEMINI_API_KEY`, có thể xóa dòng đó (backend bỏ qua) và nên thu hồi key trong Google AI Studio.

Đóng gói source và dữ liệu tổng hợp bằng Python, không đưa runtime data/key vào ZIP:

```powershell
python -m scripts.package_submission
# Hoặc từ thư mục gốc, dùng Python trong Docker:
docker compose run --rm --no-deps --volume "${PWD}:/submission-work" --workdir /submission-work api python -m scripts.package_submission
```

Kết quả: `submission/bai1-rag.zip`. Script dùng danh sách thư mục cho phép, loại `.env`,
node_modules, build, cache, data/uploads, data/chroma, secret files và kiểm tra key
trước khi ghi archive. Không xóa `.env` hoặc dữ liệu local.

## Retrieval audit

`evaluation/results_baseline.json` và `results_retrieval.json` giữ số đo trước/sau.
`retrieval_diagnostic.json` chứa text/OCR/chunks, raw Top-K và threshold sweep trên
corpus tổng hợp. `chunk_sweep.json` ghi thử nghiệm kích thước chunk.
Raw ranking: Hit@3 = Hit@4 = 100%; nhiều chunk đúng có score dưới 0.55, nên `/ask`
không dùng threshold để từ chối (chi tiết trong REPORT_RETRIEVAL_GATING.md).

## Diagnostic upload → retrieval (local, không gọi LLM)

```powershell
docker compose run --rm api python -m scripts.diagnose_upload
docker compose run --rm api python -m scripts.diagnose_upload --file /app/sample_data/sample.pdf --question "How many days of annual leave do employees receive?"
```

Script dùng production services trong DB riêng, đo count trước/sau, readback, vector
shape/finite/norm, raw/filtered retrieval và context nhận bởi `/ask`. Generation được
thay bằng context spy có ghi nhãn; đây không phải live LLM test. Mặc định không
in text tài liệu; `--preview` chủ động bật đoạn đầu tối đa 100 ký tự/chunk.
DB diagnostic ở cache, không đụng Chroma đang phục vụ người dùng.
