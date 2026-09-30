# Sửa retrieval gating & chẩn đoán Gemini — 29/09/2026

## 1. Root cause retrieval

**CASE A — ranking đúng, fixed threshold sai vai trò.** Không phải lỗi upload/Chroma,
không phải lỗi công thức `relevance = 1 - distance` (giữ nguyên).

- Raw Top-K (trước threshold), 39 câu = 25 câu evaluation + 14 câu trên PDF đề bài người dùng:
  **Hit@1 90.3%, Hit@3 100%, Hit@4 100%, MRR 0.946**. Không có câu nào relevant chunk nằm ngoài Top-K.
- Nhưng chỉ **74.2%** relevant chunk vượt `RETRIEVAL_THRESHOLD=0.55`; trên PDF đề bài chỉ **4/10**.
- `/api/questions/ask` gọi `retrieve()` đã lọc 0.55 → `contexts=[]` → bỏ qua Gemini → trả no-answer.
- Score relevant và no-answer **chồng lấn**: relevant thấp nhất 0.234 (“bài toán 2 là gì”, rank 3),
  no-answer cao nhất 0.533 (“Orion hotel Wi-Fi password”). Không threshold toàn cục nào tách được.

| Top-1 / relevant score | n | min | median | max |
|---|---:|---:|---:|---:|
| Relevant chunk (answerable) | 31 | 0.234 | 0.651 | 0.829 |
| Answerable Top-1 | 31 | 0.306 | 0.651 | 0.829 |
| No-answer Top-1 | 8 | 0.216 | 0.299 | 0.533 |

Chỉ riêng 25 câu evaluation: Hit@1 95.2%, Hit@3/Hit@4 100%, MRR 0.976; threshold cũ 90.5%.

**Candidate floor:** sweep 0.00→0.60. Floor ≤0.20 không loại được no-answer nào; floor ≥0.25
bắt đầu loại relevant chunk. Không có bằng chứng floor giúp → `CANDIDATE_MIN_RELEVANCE=-1` (tắt).

**Chunking (Phase 5):** `CHUNK_SIZE=700` bị cap còn **126 token** (MiniLM `max_seq_length=128`
− 2 special tokens), overlap hiệu dụng 22. PDF đề bài → 6 chunk; heading “Bài toán 2 …” rơi vào
cuối chunk 1, mô tả ở chunk 2 (fragmentation có thật). Thử overlap ~50 và ~68 token trong DB test:
Hit@1 0.935 / 0.871 (baseline 0.903), Hit@3/4 vẫn 100% → không cải thiện rõ, **giữ production**.
Phase 6 (A/B embedding) không cần vì ranking không hỏng; A/B trước đó xem `REPORT_EMBEDDING_AB.md`.

## 2. Root cause Gemini

`scripts/check_gemini.py` (chỉ text tổng hợp “Project Orion has internal code ZXQ-91827.”):

- Key có, đúng định dạng Auth Key hiện hành; `models.list` **thành công**; `gemini-3.5-flash` có trong danh sách.
- `generateContent` → **HTTP 403 `PERMISSION_DENIED`: “Your project has been denied access. Please contact support.”**

→ Project Google Cloud đứng sau key bị Google chặn ở cấp project. Không phải key sai (sẽ là 400/401),
không phải model (404), không phải quota (429). Đổi model/code/Docker không sửa được. **BLOCKED EXTERNAL.**

Lưu ý: `docker run --env-file .env` giữ nguyên dấu nháy và CR (file CRLF) → cho kết quả 400 giả;
compose và app (python-dotenv) đọc đúng. Chạy diagnostic qua compose hoặc để app tự đọc `.env`.

## 3. Files changed

- `app/core/config.py` — thêm `candidate_min_relevance` (mặc định -1).
- `app/services/retrieval.py` — `raw_search()`, `search_candidates()` (raw Top-K, không lọc 0.55); `retrieve()` giữ cho legacy.
- `app/services/generation.py` — `verify()` trả `GeneratedAnswer(supported, answer, context_indexes)`; schema gửi Gemini bắt buộc `context_indexes`; prompt nói rõ candidates có thể không liên quan; tách 401/400-invalid-key (`gemini_auth_error`), 403 project (`gemini_project_access_denied`), 403 khác (`gemini_permission_denied`) — đều 503.
- `app/services/answering.py` — phân biệt không candidate / unsupported / supported; nguồn chỉ từ chunk Gemini đánh dấu.
- `app/api/questions.py` — `/api/questions/ask` dùng `search_candidates()`.
- `scripts/check_gemini.py` — viết lại (synthetic, phân loại lỗi, lỗi đã làm sạch, grounded tests, user action).
- `scripts/evaluate.py` — `retrieval_ranking` (Hit@1/3/4, MRR, phân phối, floor sweep) và `answering` tách riêng; `--extra-cases`.
- `scripts/reproduce_retrieval_gate.py` — mới, tái hiện trên DB riêng.
- `tests/test_candidate_gating.py` — mới (A–F + schema + floor); cập nhật `test_ask_api.py`, `test_generation.py`, `test_gemini_diagnostic.py`, `test_e2e.py`, `test_upload_retrieval_regression.py`.
- `README.md`, `.env.example`, `evaluation/results_retrieval.json` (chạy lại).

Frontend không cần sửa: `api.js` đã hiện `detail` của mọi response không-2xx (503 Gemini hiện lỗi Gemini);
câu no-answer chỉ hiện khi backend trả 200 với câu đó. Upload và Ask cùng dùng `VITE_API_URL`.

## 4. Luồng cũ / mới

```text
CŨ:  Question → embed → Top-4 → lọc relevance ≥ 0.55 → [] → bỏ qua Gemini → "Không tìm thấy…"
MỚI: Question → embed → raw Top-4 (search_candidates)
       ├─ không có candidate (collection rỗng) → no-answer, không gọi Gemini        (A)
       └─ Gemini grounded verification
            ├─ supported=false → no-answer                                         (B)
            ├─ supported=true  → answer + sources theo context_indexes
            └─ lỗi Gemini 401/403/404/429/network → HTTP 5xx, KHÔNG no-answer        (C)
```

## 5. Kết quả

| Hạng mục | Kết quả |
|---|---|
| Upload PDF tổng hợp → Chroma | PASS (1 chunk, ghi xác minh, count=1) |
| Raw Top-K có relevant chunk | PASS (rank 1, 0.676; 39 câu Hit@4 100%) |
| Candidate < 0.55 vẫn tới Gemini | PASS (test A + mutation: trả lại `retrieve()` làm 6 test fail) |
| Gemini 403 → 503, không no-answer | PASS (HTTP thật trên backend test :8001) |
| Gemini synthetic connectivity | **FAIL — 403 project denied (external)** |
| Grounded test (supported true/false) | Chưa chạy được — chặn bởi 403 |
| E2E có câu trả lời ZXQ-91827 / Beta → no-answer | **BLOCKED** bởi Gemini 403 (luồng tới Gemini đã xác minh) |
| PDF thực tế (fact/paraphrase/no-answer) | Retrieval PASS (Hit@3 10/10); câu trả lời BLOCKED bởi 403 |
| Test suite | **100 passed, 0 failed, 2 skipped** (2 live Gemini opt-in) |

## 6. Việc người dùng cần làm (Gemini)

1. Google AI Studio → API Keys: kiểm tra project của key đang dùng; project hiện bị “denied access”.
   Liên hệ/khiếu nại Google cho project đó, **hoặc** tạo API key mới trong một project khác không bị hạn chế.
2. Thay `GEMINI_API_KEY` trong `.env` (giữ `GEMINI_MODEL=gemini-3.5-flash`, model có trong danh sách).
3. `docker compose up -d --force-recreate api`
4. `docker compose run --rm api python -m scripts.check_gemini` → cần thấy
   `GEMINI CONNECTIVITY PASS` và `GEMINI GROUNDED BEHAVIOR PASS`.

## 7. Lưu ý đánh đổi

Vì raw Top-K luôn được gửi Gemini, câu hỏi ngoài tài liệu giờ cũng gọi Gemini (tốn một request) và
text của tối đa TOP_K chunk — kể cả chunk ít liên quan — được gửi đi. Nếu Chroma chứa tài liệu nhạy cảm
(ví dụ CV), chunk của nó có thể nằm trong Top-K của câu hỏi không liên quan. Muốn giảm, dùng
`CANDIDATE_MIN_RELEVANCE` sau khi đo lại bằng `scripts.evaluate` (sweep floor), hoặc xoá tài liệu đó khỏi DB.
