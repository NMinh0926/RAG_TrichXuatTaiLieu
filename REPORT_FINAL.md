# Báo cáo hoàn thiện — 29/09/2026

## 1. SUMMARY

Đã hoàn thiện frontend React/Vite hai trang, CORS local, diagnostic Gemini và helper
đóng gói an toàn. Giữ pipeline extraction/OCR → cleaning → token chunking →
SentenceTransformer → ChromaDB → retrieval → Gemini, cùng endpoint Phase 1.

Backend và frontend dev server đang chạy tại `127.0.0.1:8000` và `localhost:5173`.
**Chưa xác nhận toàn bộ acceptance:** generation thật bị Google project 403;
browser integration chưa chạy được vì công cụ không có browser kết nối. Không
đánh dấu hai phần này PASS. Retrieval còn 2 miss, đã điều tra và giữ cấu hình an toàn.

## 2. FILES CHANGED

Sửa:

- `.gitignore`: loại env variants, node_modules, build, ZIP, logs, submission.
- `.env.example`: bổ sung FRONTEND_ORIGINS; key vẫn là `your_api_key`.
- `app/core/config.py`: cấu hình origin frontend.
- `app/main.py`: CORS middleware, không credentials.
- `README.md`: frontend, local/Docker commands, diagnostic, demo, security, số đo thật.
- `evaluation/results_retrieval.json`: kết quả lần chạy cuối.

Tạo:

- `scripts/check_gemini.py`
- `scripts/package_submission.py`
- `scripts/inspect_retrieval.py`
- `tests/test_cors.py`
- `tests/test_gemini_diagnostic.py`
- `tests/test_submission.py`
- `evaluation/results_baseline.json`
- `evaluation/retrieval_diagnostic.json`
- `evaluation/chunk_sweep.json`
- `frontend/.env.example`
- `frontend/package.json`, `frontend/package-lock.json`
- `frontend/index.html`, `frontend/vite.config.js`
- `frontend/src/main.jsx`, `frontend/src/App.jsx`, `frontend/src/styles.css`
- `frontend/src/services/api.js`
- `frontend/src/components/LoadingIndicator.jsx`
- `frontend/src/components/SourceCard.jsx`
- `frontend/src/pages/DocumentsPage.jsx`
- `frontend/src/pages/AskPage.jsx`
- `REPORT_FINAL.md`
- `submission/bai1-rag.zip` (artifact, ignored; không nằm trong chính archive).

Không sửa `.env` local, evaluation questions/corpus, generation service, loaders,
chunker, embedding, retrieval, vector store, Dockerfile hoặc compose.yaml.

## 3. GEMINI STATUS

| Mục | Kết quả |
|---|---|
| Configured model | `gemini-3.5-flash` |
| Timeout | 30 giây |
| Key | Có ở backend; không in giá trị |
| Official SDK list models | PASS; configured model có trong danh sách |
| Generation nhỏ `Reply with OK.` | FAIL: HTTP 403 |
| Exact category | `project_access_denied` / backend `gemini_project_access_denied` |
| External blocker | **BLOCKED EXTERNAL — Google project bị từ chối quyền truy cập** |
| Live tests cuối | 2 failed, 0 passed, 0 skipped; 2.06 giây |

List model thành công không có nghĩa project được phép generation. Không đổi
provider hoặc tự động chọn model khác. Chủ project cần xử lý quyền với Google.
Không chạy 25 lượt generation khi diagnostic đã xác nhận blocker.

## 4. RETRIEVAL BEFORE / AFTER

| Metric | Before | After |
|---|---:|---:|
| Total / answerable | 25 / 21 | 25 / 21 |
| Retrieval hit | 19/21 | 19/21 |
| Hit rate | 90.48% | 90.48% |
| No-answer có retrieval rỗng | 4/4 | 4/4 |
| Threshold | 0.55 | 0.55 |
| Top-K | 4 | 4 |
| Pipeline errors | 0 | 0 |

Hai câu vẫn miss sau điều tra:

- **Nova Phone:** expected `operations.xlsx`, sheet Sales, doanh thu 200. Text
  `Product: Nova Phone | Revenue: 200` đầy đủ trong chunk. Raw rank 1,
  relevance **0.502113**, bị threshold loại.
- **Vega emergency extension:** expected `warehouse_scan.pdf`, page 1, số 456.
  PaddleOCR nhận đúng `The Vega emergency extension is 456.`; nguyên câu trong
  chunk. Raw rank 1, relevance **0.530722**, bị threshold loại.

Không mất text/OCR, không bị cắt đáp án, không lỗi Top-K. Chunk gộp nhiều thông tin
và độ tương đồng embedding hiện tại chưa vượt ngưỡng; không có bằng chứng cần sửa
extraction hay thay architecture.

Threshold sweep trên toàn bộ 25 câu:

| Threshold | Hit / 21 | No-answer rỗng / 4 |
|---|---:|---:|
| 0.40–0.50 (bước 0.01) | 21 | 3 |
| 0.51–0.53 | 20 | 3 |
| 0.54–0.60 | 19 | 4 |

Câu no-answer Wi-Fi có raw similarity **0.532934**, cao hơn cả hai câu miss.
Không có một threshold chung vừa lấy lại chúng vừa giữ cả 4 no-answer rỗng.
Vì vậy **không hạ ngưỡng để tăng điểm**.

Thử chunk size 48/64/80/96/112/700, overlap cùng tỷ lệ cấu hình, max model 128,
đều cho 19/21 và 4/4 no-answer tại threshold 0.55. So sánh này dùng normalized
embeddings và cosine trên toàn bộ chunk của corpus; kết quả baseline/final chính
vẫn chạy bằng Chroma. Giữ chunk size 700/overlap 120, được cap theo model như cũ.
Không đổi embedding model, không hard-code từ/câu hỏi và không sửa dataset.

## 5. BACKEND TEST RESULT

- Baseline local: **66 passed, 0 failed, 2 skipped**, 2 warnings, 119.24 giây.
- Final local: **80 passed, 0 failed, 2 skipped**, 2 warnings, 120.08 giây.
- Final live Gemini opt-in riêng: **0 passed, 2 failed, 0 skipped**, 2.06 giây.
- Hai test skip trong local suite là live Gemini opt-in; đã chạy thật riêng, không
  đổi lỗi thành skip hoặc pass.
- OCR/embedding/Chroma/persistence chạy thật. Generation trong unit/API/E2E tests
  dùng mocks có ghi nhãn như baseline, không phải bằng chứng generation thật.
- Hai warnings là Starlette TestClient/httpx deprecation và Paddle thiếu ccache.

Tests thêm kiểm chứng CORS allowed/disallowed origin, response lỗi có CORS,
diagnostic phân loại lỗi/redaction và archive loại runtime/secret, chặn literal key.

## 6. FRONTEND RESULT

- `npm install`: PASS, 68 packages added, audit 0 vulnerabilities tại lần chạy.
- `npm run build`: **PASS**, Vite 7.3.6, 46 modules; không có lint script.
- `/documents` và `/ask`: dev server trả HTTP 200.
- Đã implement loading, disable thao tác trùng, success, lỗi mạng/backend, no-answer,
  answer và source filename/page/sheet; không lưu chat history.
- **Browser integration: NOT VERIFIED.** CUA trả `apps: []`, `browsers: []`.
  Không tuyên bố đã click/upload hoặc kiểm tra hình ảnh giao diện trong browser.
- **Answer + sources live: BLOCKED EXTERNAL** bởi Google 403. API/E2E mock tests
  kiểm tra contract nguồn; chưa có câu trả lời thật để kiểm tra trực quan trên UI.

HTTP integration qua server thật với Origin `http://localhost:5173`:

| Case | Kết quả |
|---|---|
| `/health` | HTTP 200 |
| sample.pdf | HTTP 200, 1 chunk |
| sample.docx | HTTP 200, 1 chunk |
| sample.xlsx | HTTP 200, 2 chunks |
| sample.jpg | HTTP 200, 1 chunk, OCR thật |
| sample.png | HTTP 200, 1 chunk, OCR thật |
| sample_scan.pdf | HTTP 200, 1 chunk, OCR thật |
| Earth–Mars question | HTTP 200, câu no-answer chuẩn, sources=[] |
| Annual leave question | HTTP 503, `gemini_project_access_denied`, detail an toàn |
| CORS các upload/ask trên | Origin được trả đúng |

HTTP integration không được tính là browser integration.

## 7. EVALUATION

`python -m scripts.evaluate --retrieval-only` chạy thật trước và sau thay đổi.
Total 25, answerable 21, retrieval hit 19, hit rate 90.48%, no-answer empty 4/4,
pipeline errors 0. Correct answer count / answer accuracy / no-answer generation
accuracy: **not evaluated (`null`)** do external 403.

SHA256 `evaluation/questions.json` không đổi:
`760cc317ab4ae1545d696305f9111fbdbf80ce3b64feaa00cf6aa5f8217d3de9`.
Các corpus file cũng được đối chiếu SHA256 trước/sau; không tái tạo dataset.

## 8. SECURITY

- Quét source/tests/scripts/docs/evaluation/frontend và artifact không phát hiện
  literal key local hoặc Google API key pattern; không hiển thị key.
- `.env`, frontend env và variants được Git ignore; `.env.example` chỉ placeholder.
- Git ở thư mục cha Documents; không có commit/history cho đường dẫn project,
  không có file project được track ở thời điểm audit.
- Key chỉ nằm backend; frontend không có Gemini config/request. Browser chỉ gọi FastAPI.
- Local `.env` giữ nguyên. ZIP dùng allowlist, loại env, cache, venv, node_modules,
  frontend build, uploads thật, Chroma local và secret files. ZIP được kiểm tra integrity.
- Key từng nằm trong ZIP cũ theo thông tin đầu vào: **nên rotate key**. Không tự
  revoke key, không tuyên bố các archive cũ ngoài project đã được làm sạch.

## 9. RUN COMMANDS

Từ thư mục gốc, với Docker (môi trường đã kiểm chứng):

```powershell
docker compose up -d --build
docker compose ps
docker compose run --rm -e RUN_LIVE_GEMINI=0 api python -m pytest -q -p no:cacheprovider
docker compose run --rm -e RUN_LIVE_GEMINI=1 api python -m pytest tests/test_live_gemini.py -q -p no:cacheprovider --tb=no
docker compose run --rm --volume "${PWD}/evaluation:/app/evaluation" api python -m scripts.evaluate --retrieval-only
docker compose run --rm api python -m scripts.check_gemini
docker compose run --rm --volume "${PWD}/evaluation:/app/evaluation" api python -m scripts.inspect_retrieval
docker compose run --rm --no-deps --volume "${PWD}:/submission-work" --workdir /submission-work api python -m scripts.package_submission
```

Chỉ sau khi diagnostic thành công mới chạy full Gemini evaluation:

```powershell
docker compose run --rm --volume "${PWD}/evaluation:/app/evaluation" api python -m scripts.evaluate
```

Backend local nếu có Python 3.11 (không cài lại Python native trong phiên này):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Trong terminal khác, với .venv đã activate:
python -m pytest -q
python -m scripts.evaluate --retrieval-only
python -m scripts.check_gemini
python -m scripts.package_submission
```

Frontend (Node 22.12+):

```powershell
cd frontend
npm install
npm run dev
npm run build
```

Máy hiện không có Node/npm trên PATH nên phiên kiểm tra dùng image `node:22-alpine`.
Lệnh Docker frontend được ghi đầy đủ trong README. Container demo hiện tại:
`docker stop bai1-rag-frontend-demo` / `docker start bai1-rag-frontend-demo`.

## 10. DEMO STEPS

1. Start backend bằng `docker compose up -d --build`.
2. Start frontend bằng `npm run dev` trong frontend, hoặc container demo đã tạo.
3. Mở `http://localhost:5173/documents`.
4. Chọn sample PDF/DOCX/XLSX/JPG/PNG/scan, bấm Nạp tài liệu; xem tên và số chunk.
5. Mở `/ask`, hỏi `Một năm người lao động có bao nhiêu ngày phép?`.
6. Khi Google cho phép generation, kiểm tra đáp án 12 ngày và nguồn. Hiện tại sẽ
   nhận lỗi quyền Google; đây là trạng thái thật, không dùng đáp án giả để demo.
7. Hỏi `What is the distance from Earth to Mars?`, kiểm tra câu no-answer và không có nguồn.

## 11. KNOWN LIMITATIONS

- External Google project permission chặn generation thật và answer metrics.
- Chưa kiểm chứng UI bằng browser, gồm tương tác upload, rendering source/error,
  loading và responsive. Build/HTTP checks đã pass nhưng không thay thế browser QA.
- Hai retrieval miss vẫn tồn tại; hạ threshold làm tăng false positive trên corpus.
- Corpus chỉ 25 câu tổng hợp, không có holdout; không suy rộng accuracy ra dữ liệu thực.
- OCR phụ thuộc chất lượng ảnh; fixtures OCR là tiếng Anh rõ nét. Chưa hiểu biểu đồ,
  tái dựng bảng scan, OCR ảnh nhúng DOCX/header/footer; Excel không tính công thức.
- XLS loader có nhưng integration fixture là XLSX. Sources là context-level,
  không phải attribution từng câu. Prompt/schema không đảm bảo không hallucinate.
- Model tải lần đầu tốn thời gian; chưa benchmark tải đồng thời.

## 12. FINAL SCOPE CONFIRMATION

Không thêm login, admin, SQL database, chat history, agent, retrieval framework,
BM25/reranker/hybrid search, document CRUD/list API, cloud deployment hoặc tính năng
ngoài scope. Không gửi tài liệu gốc trực tiếp cho Gemini. Gemini chỉ nhận question
và retrieved text; diagnostic chỉ dùng prompt nhỏ. Giữ Google Gemini provider,
SentenceTransformer, ChromaDB và backward compatibility endpoint Phase 1.
