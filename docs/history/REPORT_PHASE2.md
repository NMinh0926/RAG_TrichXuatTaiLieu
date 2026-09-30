# Báo cáo giai đoạn 2

Đã triển khai generation, Upload/Ask API, sources, error handling, logging, E2E tests,
evaluation và README. Kiến trúc extraction/OCR/chunking/embedding/Chroma/retrieval
của giai đoạn 1 được giữ nguyên.

**Chưa đạt toàn bộ Definition of Done:** hai test Gemini thật bị Google trả
`403 PERMISSION_DENIED` với thông báo project bị từ chối truy cập. Chưa thể xác nhận
correct-answer generation và no-answer có context bằng dịch vụ thật. Không thay
provider, không thay kết quả thật bằng mock, không công bố answer accuracy giả.

## Kiểm chứng

**Lượt tổng cuối cùng: 64 passed, 2 failed, 0 skipped, 2 cảnh báo trong 289.33 giây.**
Đã bật `RUN_LIVE_GEMINI=1` và chạy đủ 66 tests. Hai test fail là generation thật
và từ chối khi context không đủ; cả hai nhận lỗi Google project 403. Kết quả:
`test-results-phase2-final.xml`. Các lượt trước được giữ dưới đây để đối chiếu.

- Baseline giai đoạn 1: **28 passed**, 2 cảnh báo thư viện, 165.72 giây.
  Lượt sandbox trước đó 26 passed/2 failed do PaddleOCR không truy cập được model host;
  lượt có quyền mạng đã pass đầy đủ.
- Bộ giai đoạn 2: **63 passed, 2 skipped** trong 129.73 giây. Hai live tests mặc định
  opt-in nên skip trong lượt này; log `test-results-phase2.xml`.
- Test bổ sung dùng **SDK Google thật + HTTP MockTransport** đã **1 passed**, xác minh
  request serialize chỉ có text question/context và không có tool/file.
- Tổng số test local đã pass: **64**. OCR, embedding, Chroma dùng model/storage thật;
  generation trong unit/API/E2E tests được mock và ghi rõ trong test.
- Khi bật live Gemini với key đã được cung cấp: **2 failed**, đều là lỗi provider 403,
  chưa nhận được answer. Đây là kiểm thử thật đã thử chạy, không phải skip.
- `compileall`: pass.

## End-to-end cases

| Case | Trạng thái | Phạm vi kiểm chứng |
|---|---|---|
| PDF text upload → ask | PASS local | PDF thật, embedding/Chroma thật, Gemini mock; filename/page đúng |
| DOCX upload → ask | PASS local | Text/bảng thật, câu hỏi tiếng Việt, Gemini mock |
| Excel upload → ask | PASS local | Đúng doanh thu Laptop 150, sheet Sales; Gemini mock |
| Image upload → ask | PASS local | PaddleOCR thật, metadata ảnh; Gemini mock |
| Scan PDF upload → ask | PASS local | PaddleOCR thật, page=1; Gemini mock |
| Semantic query | PASS local | Câu hỏi khác cách diễn đạt vẫn retrieve chính sách 12 ngày |
| CEO Microsoft ngoài phạm vi | PASS local | API trả no-answer, không gọi Gemini |
| Persistence qua restart | PASS | Upload và ask ở hai tiến trình Python riêng, dùng chung Chroma path |
| Gemini failure | PASS controlled handling | Mock quota/auth/network/timeout/empty/malformed; server hồi phục |
| Empty question | PASS | HTTP 422 trước retrieval/generation |
| Gemini trả lời grounded thật | BLOCKED | Project bị Google từ chối, HTTP 403 |
| Gemini từ chối khi context không đủ | BLOCKED | Project bị Google từ chối, HTTP 403 |

## Evaluation đo thực tế

Corpus tổng hợp: 5 tài liệu (PDF, DOCX, XLSX, PNG, scan PDF). Có thêm PNG nguồn để
tái tạo scan. Dataset: 25 câu hỏi, chia 5/4/4/4/4 câu có đáp án + 4 câu no-answer.

| Metric | Kết quả |
|---|---|
| Total questions | 25 |
| Answerable questions | 21 |
| Retrieval hit count | 19/21 |
| Retrieval hit rate | **90.48%** |
| Correct answer count | **Chưa đo được** (`null`) |
| Answer accuracy | **Chưa đo được** (`null`) |
| No-answer accuracy toàn pipeline Gemini | **Chưa đo được** (`null`) |
| No-answer queries có empty retrieval | 4/4 (chỉ là kết quả retrieval) |
| Pipeline error trong lượt retrieval-only | 0 |

Kết quả từng câu: `evaluation/results_retrieval.json`, với model, threshold và Top-K.
Hit đòi đúng source/page/sheet và keyword trong cùng chunk. Không tính 4 câu no-answer
vào mẫu số retrieval hit rate. Hai câu bỏ sót: doanh thu Nova Phone và số nội bộ khẩn
cấp Vega; cả hai bị lọc theo ngưỡng 0.55. Không đổi ngưỡng chỉ để tăng điểm bộ mẫu.
Chưa chạy 25 lượt generation với key lỗi đã biết; cần key/project có quyền truy cập.

## File thay đổi

File mới:

```text
app/services/generation.py
app/services/answering.py
scripts/create_evaluation_data.py
scripts/evaluate.py
tests/test_generation.py
tests/test_ask_api.py
tests/test_e2e.py
tests/test_evaluation.py
tests/test_live_gemini.py
evaluation/questions.json
evaluation/results_retrieval.json
evaluation/documents/travel.pdf
evaluation/documents/hr.docx
evaluation/documents/operations.xlsx
evaluation/documents/notice.png
evaluation/documents/warehouse.png
evaluation/documents/warehouse_scan.pdf
REPORT_PHASE2.md
```

File đã sửa:

```text
app/core/config.py
app/core/dependencies.py
app/core/logging.py
app/models/schemas.py
app/api/documents.py
app/api/questions.py
app/services/ingestion.py
app/main.py
tests/conftest.py
pytest.ini
requirements.txt
requirements.lock.txt
.env.example
README.md
REPORT.md (thêm liên kết tới báo cáo hiện tại)
.env (chỉ local, đã ignore)
```

Thay đổi ngoài generation chủ yếu thêm endpoint và logging. Upload bổ sung lỗi I/O
có kiểm soát và dọn file lỗi không che mất lỗi ban đầu. Giữ endpoint phase 1 để tương
thích test/khách gọi cũ. Không đổi loader, chunker, embedding, retrieval hay vector store.

## Lệnh cài/chạy/test/evaluation

```powershell
# Cài mới sau khi tạo .venv bằng Python 3.11
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Với uv đã có trong workspace
$env:UV_CACHE_DIR="$PWD\.cache\uv"
.\.tools\uv.exe pip install --python .venv\Scripts\python.exe -r requirements.lock.txt

# Backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Tests local và opt-in Gemini thật
.\.venv\Scripts\python.exe -m pytest -v
$env:RUN_LIVE_GEMINI='1'
.\.venv\Scripts\python.exe -m pytest -v

# Evaluation
.\.venv\Scripts\python.exe -m scripts.evaluate --retrieval-only
.\.venv\Scripts\python.exe -m scripts.evaluate
```

Nếu môi trường uv chưa có pip, dùng uv hoặc chạy `python -m ensurepip --upgrade`
trong `.venv`. Chạy từ thư mục dự án; cấu hình key/model trong `.env`.
**Năm ví dụ API request** nằm ở mục cùng tên trong `README.md`: upload PDF, DOCX,
Excel, ask tiếng Việt có đáp án và ask CEO Microsoft ngoài phạm vi.

## Checklist yêu cầu

- [x] PDF, DOCX, Excel, Image, PDF scan được nạp và truy xuất.
- [x] OCR dùng PaddleOCR; embedding dùng sentence-transformers; Chroma persistent.
- [x] Gemini chỉ nhận question và retrieved text, không nhận file gốc/tool/search/memory.
- [x] Upload `/api/documents/upload` và Ask `/api/questions/ask`.
- [x] Không có context → no-answer trực tiếp, không gọi Gemini.
- [x] Có context nhưng model báo không đủ → no-answer chuẩn (đã kiểm tra bằng mock).
- [x] Sources lấy từ metadata retrieval, loại trùng filename/page/sheet.
- [x] Lỗi key/quota/network/timeout/empty response và lỗi pipeline được kiểm soát.
- [x] Logging cần thiết; không log secret hoặc nội dung riêng tư; API không trả stack trace.
- [x] E2E qua đủ định dạng và persistence qua hai tiến trình.
- [x] Dataset 25 câu và script metrics có thể chạy, retrieval đã đo thật.
- [x] README: kiến trúc, cài đặt, config, API, OCR/RAG, evaluation, mẫu và limitations.
- [x] Cleanup nhỏ, không thêm frontend, agent, framework RAG, DB hay feature ngoài phạm vi.
- [ ] Gemini generation thật trả lời đúng — cần giải quyết Google project 403.
- [ ] Evaluation answer accuracy và no-answer accuracy thật — cùng blocker trên.
- [ ] Tất cả live tests pass — hiện 2 fail do provider.

## Kiểm tra secret

Key người dùng đã điền vào `.env.example` được chuyển sang `.env`; file mẫu đã trả về
`your_api_key`. Không hiển thị key trong output. Kiểm tra literal key trong source,
tests, scripts, evaluation và tài liệu chia sẻ không thấy key. `.env` có trong
`.gitignore`; Git không track `.env` hoặc `.env.example` tại thời điểm kiểm tra.
Repository Git nằm ở thư mục cha `Documents`; chưa có history cho project này,
nên không phát hiện key trong history hiện có của đường dẫn project.

## Limitations

Google project 403 là blocker bên ngoài code. Không thể xác nhận khả năng generation
thật trước khi được cấp quyền. Prompt/schema không chứng minh mọi câu trả lời đều
được grounding; keyword evaluation cũng có thể bỏ lọt sai nghĩa. Các sources là
context được cung cấp, chưa trích dẫn từng câu. Fixed threshold bỏ sót 2/21 câu mẫu.
OCR mới kiểm tra chữ in rõ nét; chưa đánh giá scan tiếng Việt thực tế. `.xls` có
loader nhưng fixture Excel là `.xlsx`; DOCX không OCR ảnh nhúng/header/footer;
Excel không tính công thức. Không có CRUD, quản lý phiên bản, chat history hay frontend.

Dừng đúng phạm vi giai đoạn 2; không triển khai feature bổ sung.
