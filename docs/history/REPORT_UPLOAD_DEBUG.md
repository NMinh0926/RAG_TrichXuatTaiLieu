# Upload thành công nhưng câu hỏi trả no-answer — 29/09/2026

**Trạng thái: đã xác định và tái hiện nguyên nhân; chưa sửa được false-negative của
câu hỏi thực tế trong các ràng buộc hiện tại.** Các thay đổi dưới đây bổ sung kiểm
chứng ghi dữ liệu, validation và logging; không được coi là bản sửa retrieval.

## 1. ROOT CAUSE

File người dùng chỉ định: `data/uploads/5a6a363f94d947a5ad17a17389f47508.pdf`.
Tên nguồn trong Chroma được đối chiếu bằng SHA256 của tên gốc + bytes file.
Câu hỏi thực tế: **“bài toán 1 là gì”**.

Chunk chứa tiêu đề đúng đứng hạng 1, nhưng cosine similarity chỉ **0.305913568**.
Filter `relevance >= 0.55` loại toàn bộ 4 raw candidates. `/ask` nhận `[]` và trả
no-answer ngay; Gemini không được gọi. Đây là false-negative của semantic retrieval
với câu hỏi tham chiếu ngắn và model/config hiện tại, không phải mất dữ liệu upload.

Đã loại trừ:

- Extraction: một trang text, 2.107 ký tự; tiêu đề cần hỏi tồn tại.
- Cleaning: còn 1.987 ký tự sau chuẩn hóa whitespace; không làm mất tiêu đề/đáp án.
- Chunking: 6 chunk không rỗng, tiêu đề đầy đủ trong chunk 0; không vượt budget model.
- Embedding: 6 vector 384 chiều, finite, norm xấp xỉ 1; query cũng 384 chiều.
- Persistence: đọc lại được đúng cả 6 chunk và metadata.
- Path/collection/lifecycle: ingestion và retrieval dùng cùng instance store,
  `/app/data/chroma`, collection `documents_85d5decab4479bc4`, `default_tenant`,
  `default_database`; dùng PersistentClient.
- Docker: một service API, bind mount `./data:/app/data`.
- Metric: Chroma 1.5.9 cosine trả `1 - dot(x,y)/(norm(x)*norm(y))`; code đổi lại
  `relevance=1-distance` đúng. Không đảo nhầm distance/similarity.
- Không có `where`/`where_document` trong retrieval query; metadata không loại file.
- Upload hiện có đã đợi ingestion/upsert hoàn thành trước khi trả success.
- Frontend dùng một API module và cùng backend cho upload/ask.

Không thể sửa an toàn chỉ bằng một threshold chung: câu **“bài toán 3 là gì”**
(không có trong PDF) có top score **0.322442114**, cao hơn câu đúng bài toán 1.
Các ngưỡng 0.40/0.45/0.50/0.55 đều loại câu đúng; 0.30 giữ câu đúng nhưng cũng nhận
context cho bài toán 3 và câu hỏi SQL không được tài liệu quy định.

Thử chia chunk 16/24/32/48/64/96/126 token: best score của câu đúng chỉ khoảng
0.4181, chưa vượt 0.55; chunk nhỏ cũng có thể đưa đoạn không liên quan lên đầu.
Tiêu đề đứng riêng đạt 0.3416. Vì vậy không đổi chunking hoặc hạ threshold để làm
một ví dụ pass. Không có bằng chứng runtime/model weights bị hỏng; đây là hạn chế
độ phân biệt ngữ nghĩa, không được gọi là lỗi cài đặt embedding.

## 2. FILES CHANGED

- `app/services/vector_store.py`: resolve absolute path, log collection/tenant/database/
  count; kiểm tra số chunk/vector; readback từng batch để đối chiếu IDs/text/metadata
  trước khi báo thành công; log count trước/sau. Upsert trùng vẫn hợp lệ, không yêu
  cầu count luôn tăng khi nạp lại cùng file.
- `app/services/embedding.py`: từ chối vector thiếu/rỗng, sai shape, NaN/Inf hoặc zero norm.
- `app/services/ingestion.py`: DEBUG độ dài trước/sau clean và metadata vị trí;
  INFO số embedding/chiều vector. Không log nội dung tài liệu.
- `app/services/retrieval.py`: log raw/filtered candidate count, threshold và top scores.
- `tests/test_e2e.py`: bổ sung count/readback/raw search/filtered search cho 5 định dạng.
- `tests/test_upload_retrieval_regression.py`: production dependency wiring với fact
  nghỉ phép và unique fact ZXQ-91827; mock generation ghi nhãn; kiểm tra silent write
  và invalid embeddings bị chặn.
- `scripts/diagnose_upload.py`: CLI local dùng DB riêng; `/ask` dùng context spy
  không có mạng, không gọi Gemini. Mặc định không in nội dung chunk; `--preview`
  chủ động bật tối đa 100 ký tự/chunk.
- `README.md`, `REPORT_UPLOAD_DEBUG.md`: hướng dẫn và số đo thật.

Model, threshold 0.55, Top-K=4, collection signature, chunking, API contract và
frontend không đổi. Các kiểm tra readback mới là bảo vệ bổ sung, không phải bằng
chứng rằng Chroma đã làm mất dữ liệu trong ca này.

## 3. BEFORE

Production Chroma có **13 records**, gồm **6 records** của PDF bị báo lỗi và 7
records từ các file mẫu. Không xóa hoặc thay đổi dữ liệu này khi chẩn đoán.

| Rank | Chunk | Distance | Relevance |
|---|---:|---:|---:|
| 1 | 0 — có tiêu đề đúng | 0.694086432 | 0.305913568 |
| 2 | 1 | 0.764970183 | 0.235029817 |
| 3 | 2 | 0.783244014 | 0.216755986 |
| 4 | 3 | 0.799509466 | 0.200490534 |

Filtered retrieval: **0**. Chưa gọi Gemini với nội dung PDF; lần gọi có khả năng
gửi context ra Google bị automatic approval review chặn. Tiếp tục bằng diagnostic
local với dependency generation thay bằng context spy, không truyền tài liệu ra ngoài.

## 4. AFTER

Sau bổ sung validation/readback/logging, tái nạp cùng PDF vào DB test riêng:

- Upload: HTTP 200, `status=success`, `chunks_created=6`.
- Collection count: **0 → 6**.
- `collection.get`: đủ 6 IDs; stored documents khớp chunks; vectors `(6, 384)` finite.
- Raw rank 1: chunk 0, distance **0.694086432**, relevance **0.305913568**.
- Filtered: **0**, không đổi.
- `/ask`: HTTP 200, no-answer, `sources=[]`; context spy nhận **0** context.
- **Ca thực tế vẫn FAIL acceptance retrieval. Không tuyên bố đã fix.**

Ca kiểm soát một fact tiếng Việt, trong DB khác:

- Fact: “Nhân viên chính thức được nghỉ phép 12 ngày mỗi năm.”
- Question: “Một năm nhân viên được nghỉ bao nhiêu ngày phép?”
- Count **0 → 1**; một DocumentUnit, một chunk, một vector 384 chiều; readback khớp.
- Raw distance **0.353454173**, relevance **0.646545827**; filtered giữ **1** chunk.
- Context spy được `/ask` gọi với **1** context. Marker của spy không phải đáp án Gemini.
- CEO Microsoft: raw relevance **0.027811885**, filtered **0**.

Lượt thăm dò ban đầu qua PowerShell stdin không đặt UTF-8 đã làm dấu tiếng Việt
trong fixture thành `?`; số đo từ lượt đó bị loại khỏi kết luận. Các số trên dùng
Python source UTF-8 và câu hỏi đúng dấu.

## 5. TEST RESULTS

- Regression + E2E + pipeline: **26 passed**, 0 failed, 77.04 giây.
- Unique fact `ZXQ-91827`: count tăng, stored content tồn tại, retrieval giữ đúng
  chunk, `/ask` nhận context và trả source đúng bằng Gemini mock có ghi nhãn.
- Năm định dạng PDF text/DOCX/Excel/Image OCR/scan PDF: upload → stored fact →
  raw → filtered retrieval → mocked generation đều pass, không chỉ kiểm tra upload.
- Ca PDF thực tế với câu ngắn: vẫn no-answer; không đưa file người dùng vào fixtures
  hoặc thay test kỳ vọng thành PASS giả.
- Full suite: **88 passed, 0 failed, 2 skipped**, 2 warnings, 84.33 giây.
  Hai skip là live Gemini opt-in; không phải test retrieval bị bỏ qua.

## 6. FRONTEND CHECK

Upload: `http://127.0.0.1:8000/api/documents/upload`.
Ask: `http://127.0.0.1:8000/api/questions/ask`.

Cả hai dùng `frontend/src/services/api.js`, cùng `VITE_API_URL`/default URL; không
có `.env` frontend override trong lần audit. Không sửa frontend để che lỗi backend.
Browser flow chưa được kiểm chứng; backend ca thực tế vẫn fail retrieval, nên chưa
chuyển sang đánh dấu browser acceptance PASS.

## 7. COMMANDS

```powershell
# Backend
docker compose up -d --build
# Frontend đang có trong Docker
docker start bai1-rag-frontend-demo
# Hoặc Node local
cd frontend
npm run dev
# Các lệnh dưới chạy từ thư mục gốc project
docker compose run --rm api python -m pytest tests/test_upload_retrieval_regression.py -q -p no:cacheprovider
docker compose run --rm -e RUN_LIVE_GEMINI=0 api python -m pytest -q -p no:cacheprovider
# Diagnostic một fact mặc định: không gọi Gemini
docker compose run --rm api python -m scripts.diagnose_upload
# Diagnostic PDF thực tế: chỉ local, DB riêng, context spy không có mạng
docker compose run --rm api python -m scripts.diagnose_upload --file /app/data/uploads/5a6a363f94d947a5ad17a17389f47508.pdf --question "bài toán 1 là gì"
```

Diagnostic lưu DB riêng trong `.cache/upload-diagnostic/<uuid>`, không ghi đè
production Chroma. Dữ liệu runtime này không vào submission archive.

## 8. SCOPE CONFIRMATION

Không thêm BM25, reranker, hybrid search, database mới, LLM/provider mới hoặc tính
năng sản phẩm. Không hard-code câu hỏi/nội dung trong production. Fact cụ thể chỉ
nằm trong test và diagnostic fixture. Không đổi embedding model/threshold khi chưa
có phương án vượt qua kiểm tra đúng/no-answer. Một thử nghiệm model retrieval khác
trong DB riêng là hướng cần đánh giá tiếp nếu phạm vi cho phép; không tự thay
production model hoặc xóa/reindex dữ liệu người dùng.
