# Báo cáo triển khai giai đoạn 1

Đây là báo cáo lịch sử của giai đoạn 1. Trạng thái hiện tại và tích hợp Gemini
được ghi trong [REPORT_PHASE2.md](REPORT_PHASE2.md).

Repository ban đầu trống. Đã tạo backend theo đúng phạm vi retrieval-only trong yêu cầu
đính kèm; không sửa hoặc xóa mã nguồn có sẵn.

## File đã tạo

```text
app/__init__.py
app/main.py
app/api/__init__.py
app/api/documents.py
app/api/questions.py
app/core/__init__.py
app/core/config.py
app/core/dependencies.py
app/core/errors.py
app/core/logging.py
app/models/__init__.py
app/models/schemas.py
app/services/__init__.py
app/services/file_router.py
app/services/pdf_loader.py
app/services/docx_loader.py
app/services/excel_loader.py
app/services/image_loader.py
app/services/ocr_service.py
app/services/text_cleaner.py
app/services/chunking.py
app/services/embedding.py
app/services/vector_store.py
app/services/ingestion.py
app/services/retrieval.py
scripts/__init__.py
scripts/create_samples.py
tests/__init__.py
tests/conftest.py
tests/test_loaders.py
tests/test_pipeline.py
tests/test_api.py
tests/test_integration.py
sample_data/.gitkeep
sample_data/sample.pdf
sample_data/sample_scan.pdf
sample_data/sample.docx
sample_data/sample.xlsx
sample_data/sample.jpg
sample_data/sample.png
data/uploads/.gitkeep
data/chroma/.gitkeep
.env.example
.gitignore
pytest.ini
requirements.txt
requirements.lock.txt
README.md
REPORT.md
```

Môi trường cục bộ được tạo thêm: `.env`, `.venv/`, `.tools/`, `.cache/`, `uv.zip`,
log XML pytest. Các file môi trường/cache không được đưa vào Git.

File cũ đã sửa: **không có**. Những lần chỉnh sửa trong phiên đều thuộc file mới tạo.

## Kiến trúc

`FastAPI → file_router → loader/OCR → DocumentUnit → cleaning → token window + overlap
→ sentence-transformers → ChromaDB PersistentClient`.

`Câu hỏi → cùng model embedding → cosine search → Top-K → ngưỡng → ngữ cảnh + metadata`.

PDF OCR theo từng trang, DOCX có bảng, Excel tách theo sheet, ảnh dùng PaddleOCR chung.
Model tải lazy và dùng lại. Chroma giữ dữ liệu khi tạo lại client; hash ID giúp nạp lại
cùng file không nhân đôi vector. API có xử lý lỗi file và lỗi từng bước pipeline.

## Các lệnh

```powershell
# Nếu chưa có môi trường
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Hoặc dùng requirements.lock.txt để tái lập phiên bản đã kiểm thử
Copy-Item .env.example .env

# Chạy ứng dụng
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Tạo mẫu và chạy toàn bộ test
.\.venv\Scripts\python.exe -m scripts.create_samples
.\.venv\Scripts\python.exe -m pytest -v
```

Máy ban đầu không có Python trên PATH; đã tạo Python 3.11.16 và môi trường `.venv`
riêng cho dự án. Phiên cài dùng uv; nếu cần cài lại bằng uv có sẵn:

```powershell
$env:UV_CACHE_DIR="$PWD\.cache\uv"
.\.tools\uv.exe pip install --python .venv\Scripts\python.exe -r requirements.lock.txt
```

## Kết quả kiểm thử

**Lượt chạy tổng cuối: 28 PASSED, 0 FAILED, 2 cảnh báo, trong 127.09 giây.**
Bao gồm 23 kiểm thử nhanh và 5 kiểm thử model thật, không skip test nào.
Kết quả được ghi trong `test-results-final.xml`.

Lệnh kiểm chứng cuối (model đã tải; thư mục tạm riêng để tránh xung đột quyền):

```powershell
$env:HF_HUB_OFFLINE='1'
.\.venv\Scripts\python.exe -m pytest -v --basetemp=.cache/test-final -p no:cacheprovider --junitxml=test-results-final.xml --tb=short
```

Chỉ dùng `HF_HUB_OFFLINE=1` sau khi đã tải model embedding; bỏ biến này nếu cần tải lại.

| Yêu cầu | Kết quả | Bằng chứng |
|---|---|---|
| 1. PDF text | PASS | Lấy đúng `12 days`, page=1, không gọi OCR |
| 2. PDF scan | PASS | PaddleOCR thật nhận `12` và `leave`, giữ page/source_type |
| 3. DOCX | PASS | Có paragraph tiếng Việt, bảng và thứ tự nội dung |
| 4. Excel | PASS | Hai sheet Sales/Policy và giá trị từng dòng chính xác |
| 5. Image OCR | PASS | PaddleOCR thật đọc JPG và PNG |
| 6. Chunk metadata | PASS | Giữ nguồn, index, overlap và phủ đủ nội dung |
| 7. Embedding | PASS | Model thật: 384 chiều, hữu hạn, chuẩn hóa norm=1 |
| 8. Chroma persistent | PASS | Tạo lại client vẫn tìm thấy vector, upsert không nhân đôi |
| 9. Semantic retrieval | PASS | Câu hỏi tiếng Việt tìm đúng chính sách nghỉ phép 12 ngày |
| 10. Out-of-context | PASS | Câu hỏi khoảng cách Trái Đất–sao Hỏa trả `[]` |

Kiểm tra bổ sung: health/OpenAPI, validation câu hỏi, upload quá lớn/định dạng sai,
API upload→search và hồi phục sau file hỏng, PDF trộn trang text/scan, cleaning,
cấu hình overlap sai, database rỗng, threshold, nạp trùng, tài liệu rỗng/hỏng,
DOCX không có chữ, lỗi OCR/embedding/vector store. `compileall` không báo lỗi.

## Giới hạn và vấn đề còn lại

- Chỉ trả ngữ cảnh có nguồn; chưa sinh câu trả lời, đúng phạm vi giai đoạn này.
- `.xls` đã có nhánh xlrd, nhưng mẫu/test dữ liệu Excel trong phiên dùng `.xlsx`.
- OCR đã kiểm tra bằng chữ in tiếng Anh rõ nét; chưa có tập scan tiếng Việt thực tế
  để đo độ chính xác. OCR không diễn giải biểu đồ hoặc tái dựng cấu trúc bảng scan.
- Ngưỡng cosine 0.55 đã qua ví dụ ngoài phạm vi trong test, không bảo đảm loại được
  mọi câu hỏi không liên quan. Cần kiểm định lại ngưỡng với tài liệu nội bộ thực tế.
- Chunk size 700 là mức trần cấu hình; model mặc định chỉ nhận 128 token nên chunker
  tự giới hạn phần nội dung theo tokenizer, giữ tỷ lệ overlap để tránh mất phần đuôi.
- Excel giả định hàng không rỗng đầu tiên là tiêu đề. DOCX đọc phần body và bảng,
  không OCR ảnh nhúng, không suy đoán số trang hoặc trích header/footer.
- Đổi model/chunk settings tạo collection riêng và cần nạp lại. File thay đổi nội
  dung được xem là tài liệu mới; chưa có thao tác quản lý phiên bản.
- Cảnh báo thư viện về ccache/symlink Windows và TestClient không làm hỏng các test.
- Trong lần chạy đầu đã gặp xung đột quyền thư mục tạm giữa sandbox và môi trường
  tải model; đã dùng thư mục tạm riêng trong `.cache/` cho kiểm thử model thật.
- Đã sửa lỗi MuPDF giữ khóa file PDF hỏng trên Windows bằng cách mở PDF từ bytes.

Gemini chưa được tích hợp trong giai đoạn này.
Không triển khai generation hoặc frontend. Dừng ở giai đoạn 1.
