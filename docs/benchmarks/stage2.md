# Giai đoạn 2 — Chất lượng câu trả lời (30/09/2026)

Máy đo như [baseline.md](baseline.md). LLM `qwen2.5:7b` qua API tương thích OpenAI của Ollama
(`OLLAMA_CONTEXT_LENGTH=8192`). Số liệu thô: `stage2_retrieval_ab.json`, `stage2_ingest.json`,
`stage2_qa.json`, `stage2_qa_summary3b.json`.

## 1. Retrieval A/B (trước khi chốt)

35 câu có đáp án = 21 câu bộ evaluation + 10 câu trên PDF đề bài thật của người dùng (chỉ chạy local)
+ 4 fact của PDF benchmark 36 trang. Cùng text đã trích xuất cho mọi cấu hình. "Hit" = chunk đúng nằm trong
ngữ cảnh gửi LLM.

| Cấu hình | Hit@1 | Hit@3 | Hit trong ngữ cảnh | MRR | Truy vấn (median) | Embed corpus |
|---|---:|---:|---:|---:|---:|---:|
| MiniLM, chunk 126 token, top 4 (cũ) | 77,1% | 88,6% | 88,6% | 0,824 | 28 ms | 6,9 s |
| MiniLM, top 20 → rerank bge-reranker-v2-m3 → 6 | 91,4% | 97,1% | 97,1% | 0,943 | 7 005 ms | 5,5 s |
| **bge-m3, chunk 512 token, top 6** | 91,4% | 94,3% | 94,3% | 0,929 | **124 ms** | 130 s |
| bge-m3, top 20 → rerank → 6 | 94,3% | 97,1% | 97,1% | 0,957 | 30 286 ms | 86 s |

Theo bộ câu hỏi (Hit trong ngữ cảnh): PDF đề bài thật — MiniLM top 4 **80%**, bge-m3 **100%**;
evaluation 25 câu — tất cả 100%; PDF benchmark — 50–75% (một miss của mọi cấu hình là lỗi chấm điểm:
từ khóa `850.000` so với text đã bỏ dấu phân cách, đã sửa trong script).

**Chốt:** `BAAI/bge-m3`, chunk 512 / overlap 64, `TOP_K=6`, **không bật reranker mặc định**
(`RERANKER_MODEL` để trống). Reranker thêm ~3 điểm nhưng 7–30 s mỗi câu trên CPU (GPU 4 GB đã dành cho LLM).
Đánh đổi: embedding khi nạp tài liệu chậm hơn nhiều trên CPU (bge-m3 568M tham số) — xử lý ở Giai đoạn 3.

## 2. Hỏi đáp (PDF benchmark 36 trang, đo qua HTTP thật, `/api/chat` streaming)

| Câu hỏi | TTFT | Tổng | Kết quả |
|---|---:|---:|---|
| Thử việc kỹ sư (model đang nguội) | 20,1 s | 24,5 s | ✓ "45 ngày làm việc [1]" |
| Nghỉ phép năm | 4,6 s | 9,2 s | ✓ trích [1][4], trang 10 |
| Phụ cấp ăn trưa | 4,6 s | 11,4 s | ✓ 850.000 đồng, trang 18 |
| Mã quy trình công tác phí | 5,1 s | 7,1 s | ✗ retrieval không lấy được chunk (từ chối, không bịa) |
| Giờ mở cửa kho (trang scan) | 3,1 s | 4,9 s | ✗ OCR cũ mất dấu (sửa ở Giai đoạn 3) → từ chối |
| Không có trong tài liệu | 2,5 s | 4,3 s | ✓ "Tài liệu không có đủ thông tin…" |

Baseline (Giai đoạn 0): 3/5 fact đúng, API không stream (người dùng chờ hết mới thấy chữ).
Giờ chữ đầu tiên hiện sau ~2,5–5 s khi model đã nạp.

## 3. Tóm tắt toàn tài liệu (map-reduce)

| | Ngữ cảnh dùng | Độ dài | Chữ đầu tiên | Tổng | Lần 2 (cache) |
|---|---|---:|---:|---:|---:|
| Baseline (RAG top 4) | 4/174 chunk | 1 062 ký tự | — | 38,9 s | — |
| Map + reduce đều 7b | toàn bộ 49 chunk | 4 081 ký tự | 495 s | 685 s | 0,2 s |
| **Map 3b, reduce 7b** | toàn bộ 49 chunk | 7 643 ký tự | 66 s | 413 s | 0,03 s |

Bản tóm tắt có cấu trúc Tổng quan / Các phần chính (theo chương) / Chi tiết và số liệu / Kết luận.
Khuyến nghị cho máy này: `LLM_SUMMARY_MODEL=qwen2.5:3b` (nhanh hơn 40%, không kém chi tiết). Tóm tắt được
cache theo tài liệu + model + phiên bản prompt.

## 4. Đánh giá với LLM thật (`scripts.evaluate`, 39 câu)

Corpus: 6 tài liệu evaluation + PDF đề bài của người dùng (câu hỏi/đáp án của PDF này chỉ lưu local).
Cấu hình Giai đoạn 2, `qwen2.5:7b`.

| Chỉ số | Kết quả |
|---|---:|
| Retrieval trước threshold: Hit@1 / Hit@3 / Hit@6 / MRR | 100% / 100% / 100% / 1,0 |
| Câu có đáp án trả lời đúng (từ khóa + nguồn) | **31/31 (100%)** |
| Câu không có trong tài liệu được từ chối | **7/8 (87,5%)** |
| Lỗi dịch vụ | 0 |

Câu no-answer sai: "Hạn chót nộp bài là ngày nào?" → model trả "ngày 10 tháng 10 [2]" — **bịa** (tài liệu không có
hạn chót; nhiều khả năng lấy nhầm một mốc ngày ở đoạn ngữ cảnh khác). Prompt đã cấm suy đoán nhưng model 7B
không tuân thủ tuyệt đối; nguồn trích dẫn luôn hiện để người dùng kiểm chứng. Ngoài ra câu "Công ty Sao Mai có bao
nhiêu nhân viên?" (PDF benchmark) cũng được từ chối đúng ở mục 2.
