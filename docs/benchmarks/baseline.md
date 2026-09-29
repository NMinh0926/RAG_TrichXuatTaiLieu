# Baseline — Giai đoạn 0 (29/09/2026)

Số liệu thô: [baseline_ingest.json](baseline_ingest.json), [baseline_qa.json](baseline_qa.json).
Scripts: `scripts/benchmark/` (xem lệnh trong CLAUDE.md). DB Chroma riêng trong volume cache,
không đụng `data/chroma`.

## Máy đo

| Thành phần | Giá trị |
|---|---|
| CPU | Intel Core i5-12500H (16 luồng) |
| RAM host | 15,7 GB (Docker Desktop VM: 8 GB, 16 CPU) |
| GPU | NVIDIA RTX 3050 Ti Laptop 4 GB (chỉ Ollama dùng; image backend là torch/paddle **CPU**) |
| Backend | Python 3.11.16, torch 2.14.0+cpu, paddle 3.2.0 CPU, PaddleOCR PP-OCRv5 (server det + latin mobile rec), chroma 1.5.9 |
| Embedding | paraphrase-multilingual-MiniLM-L12-v2 (384 chiều, max_seq_length 128) |
| LLM | Ollama 0.34.4 trên host, `qwen2.5:7b`, num_ctx 8192, temperature 0.2 |
| Retrieval | Top-K 4, không threshold, chunk thực tế 126 token (overlap 22) |

## File kiểm thử

`scripts.benchmark.make_test_pdf`: PDF tổng hợp, cố định, **4,11 MB, 36 trang**
(30 trang có text layer, 6 trang scan ảnh 200 dpi có nhiễu), 71 450 ký tự sau trích xuất,
**174 chunk**. 5 fact được cài ở trang 4, 10, 18, 25 (text) và 32 (scan).

## Ingest

| Bước (model đã nạp, gọi trực tiếp) | Giây | Ghi chú |
|---|---:|---|
| Đọc PDF + render trang scan | 1,03 | PyMuPDF |
| OCR 6 trang scan | **47,71** | median 8,7 s/trang |
| Clean | 0,01 | |
| Chunk | 0,10 | |
| Embedding 174 chunk | 4,95 | ~35 chunk/s, CPU |
| Ghi Chroma (+ đọc lại xác minh) | 0,22 | |
| **Tổng** | **53,99** | 0,67 trang/s |

| End-to-end `POST /api/documents/upload` | Lần 1 | Lần 2 |
|---|---:|---:|
| Cold (backend mới, nạp model trong request) | 124,3 s | 117,6 s |
| Warm (model đã nạp) | 104,3 s | 95,4 s |
| Trong request warm: OCR / embedding / Chroma | — | 88,9 / 4,9 / 0,24 s |
| Ước lượng thời gian nạp model (cold − warm) | 20,0 s | 22,3 s |
| Peak RSS tiến trình | 5,2 GB | 5,4 GB |

**OCR chậm gấp ~2 lần tùy thread.** Đo lặp 4 vòng × 3 trang scan:
thread đã khởi tạo engine PaddleOCR mất 13,9–18,6 s/trang, thread khác 7,4–13,1 s/trang
(thường 7,5–9,6). Trong app, engine được khởi tạo trong worker thread của request đầu tiên,
nên OCR trong request warm mất 14,8 s/trang so với 8 s/trang khi gọi trực tiếp.
Nguyên nhân gốc (thiết lập số luồng OpenMP/MKL của Paddle theo thread) **chưa được chứng minh**.

## Hỏi đáp (qwen2.5:7b)

Câu 1 chạy khi model đã được gỡ khỏi bộ nhớ (cold), các câu sau warm.
`LLM stream` là cùng prompt `/ask` gửi nhưng `stream=true` để đo thời gian đến token đầu (TTFT);
`/ask` là request thật của frontend (không stream).

| Câu hỏi | Retrieval | TTFT | LLM tổng | Token ra | tok/s | `/ask` | Đúng? |
|---|---:|---:|---:|---:|---:|---:|---|
| Thử việc kỹ sư (trang 4) — cold | 0,049 s | 11,91 s | 13,35 s | 16 | 11,1 | 1,78 s | ✗ chunk đúng hạng 6 |
| Nghỉ phép năm (trang 10) | 0,033 s | 1,09 s | 5,76 s | 45 | 9,6 | 5,57 s | ✓ |
| Phụ cấp ăn trưa (trang 18) | 0,030 s | 1,21 s | 5,04 s | 39 | 10,2 | 4,95 s | ✓ |
| Mã quy trình công tác phí (trang 25) | 0,041 s | 1,19 s | 4,88 s | 37 | 10,0 | 4,60 s | ✓ |
| Giờ mở cửa kho (trang 32, scan) | 0,032 s | 1,19 s | 2,74 s | 16 | 10,3 | 1,99 s | ✗ OCR mất dấu |
| Không có trong tài liệu | 0,038 s | 1,13 s | 2,81 s | 16 | 9,5 | 2,06 s | ✓ từ chối |
| **Tóm tắt toàn bộ tài liệu** | 0,051 s | 1,19 s | **58,19 s** | **429** | 7,5 | **38,86 s** | ngắn, lệch |

- Cold start LLM: `load_duration` 10,7 s. `ollama ps`: qwen2.5:7b chiếm 5,4 GB, chạy **58% CPU / 42% GPU**
  (không vừa 4 GB VRAM). qwen2.5:3b chạy 100% GPU, ~1 s/câu (đo ở phiên trước) nhưng hay quên trích nguồn.
- Prompt mỗi câu chỉ ~780–880 token dù num_ctx = 8192.
- Tóm tắt: `/ask` trả **1 062 ký tự**; ngữ cảnh chỉ gồm **4/174 chunk (2,3%)**, 4/36 trang
  (trang 2, 10, 19, 36), ~1 750 ký tự. Bản tóm tắt nói về "lưu trữ hồ sơ, đánh giá kết quả…"
  — nội dung của đúng 4 đoạn đó, không phải của tài liệu.

## Chất lượng phát hiện kèm

- **OCR tiếng Việt mất ký tự có dấu chồng**: nguồn "Kho **vật** tư Sao Mai **mở cửa** lúc 6 **giờ** 30"
  → OCR "Kho **vt** tư Sao Mai **m ca** lúc 6 **gi** 30"; "Tài sản và thiết bị" → "Tài sn và thit b".
  Model nhận dạng `latin_PP-OCRv5_mobile_rec` (OCR_LANG=vi) thiếu các ký tự ậ, ở, ử, ờ, ả, ế, ị…
  Bộ evaluation cũ chỉ có ảnh OCR tiếng Anh nên không lộ ra.
- **Retrieval trên tài liệu dài, lặp mẫu câu**: fact trang 4 đứng hạng 6 (score 0,667, top-1 0,699);
  Top-K = 4 không lấy được. Hit@4 = 100% trên bộ 25 câu cũ không phản ánh tài liệu 174 chunk.
