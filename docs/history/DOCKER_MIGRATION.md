# Chuyển dự án sang Docker

Theo yêu cầu mới, Docker thay thế môi trường Python native của dự án. Giới hạn
“không Docker” của giai đoạn trước được thay bằng yêu cầu này; pipeline RAG không đổi.

## Cấu hình

- `Dockerfile`: Python 3.11 trên Debian Bookworm, dependencies từ lockfile,
  PyTorch CPU, thư viện hệ thống OCR, chạy bằng user `appuser` UID 10001.
- `compose.yaml`: service `api`, image `bai1-rag:local`, healthcheck,
  cổng `127.0.0.1:8000`, tự khởi động lại khi Docker chạy.
- `data/` bind mount giữ nguyên uploads và Chroma trên host.
- Volume `bai1-rag_model_cache` giữ model Hugging Face/PaddleOCR qua các lần tạo lại container.
- `.env` chỉ nạp khi chạy, không copy vào image. Build context theo danh sách cho phép
  trong `.dockerignore`, không gửi `.env`, dữ liệu người dùng, cache hoặc môi trường native.

## Lệnh sử dụng

```powershell
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 api
docker compose down
```

Swagger: <http://127.0.0.1:8000/docs>.
Sau khi sửa `.env`, dùng `docker compose up -d --force-recreate`.
Không thêm `-v` vào `down` nếu muốn giữ model cache.

```powershell
# Kiểm thử local trong Linux container; HTTP tới Ollama được mock
docker compose run --rm api python -m pytest -v -p no:cacheprovider
```

## Dọn dẹp

Chỉ dọn trong thư mục dự án, sau khi cache model đã được chuyển và kiểm tra SHA256,
container đã chạy và kiểm thử thành công. Không gỡ phần mềm khác trên máy.
Mã nguồn, `.env`, `data/`, mẫu tổng hợp, evaluation và báo cáo được giữ lại.

Docker vẫn dùng dung lượng cho image và volume; dung lượng môi trường/cache host
được xóa không đồng nghĩa toàn bộ dung lượng ấy được giải phóng ròng trên ổ đĩa.

## LLM

Bước sinh câu trả lời hiện dùng Ollama chạy trên máy host (không trong container);
`compose.yaml` ánh xạ `host.docker.internal:host-gateway`. Xem README mục “Cài Ollama trên Windows”.
(Lịch sử: bản cũ dùng Gemini và bị Google chặn project — xem `REPORT_PHASE2.md`.)

## Kiểm chứng và dung lượng đã dọn

- Build image Linux thành công. Python 3.11.16, PyTorch `2.14.0+cpu`, CUDA không bật.
- Container chạy user UID 10001, không có file `/app/.env` trong image.
- Health check qua localhost thành công; thư mục persistent `data/` ghi được.
- Cache model chuyển sang volume: **54 file, 576.312.232 bytes**, đối chiếu SHA256
  giữa bản host và bản volume thành công trước khi xóa cache host.
- Kiểm thử trong Docker: **64 passed, 2 skipped, 2 cảnh báo trong 93.60 giây**.
  (Số liệu tại thời điểm chuyển đổi; hai test skip khi đó là test gọi LLM đám mây thật, nay đã bỏ.)
- Đã xóa `.venv/`, `.tools/`, `.cache/`, `.pytest_cache/`, `uv.zip`, các thư mục
  `__pycache__/` trong app/tests/scripts và sáu file XML kết quả test cũ.
- Gỡ junction uv chỉ sau khi xác minh đích nằm trong workspace. Thư mục pytest có
  quyền sandbox được dọn riêng bằng đúng quyền; không sửa ACL toàn máy.
- Lượt dọn chính xóa **3,948 GiB dữ liệu logic**. Dung lượng trống ổ C tăng quan sát
  được **2,188 GiB** trong lượt xóa (khác dung lượng logic do hardlink/cách lưu trữ).
  Đây không phải mức tăng ròng trước/sau cả việc cài Docker image và model volume.
- Mã nguồn, `.env`, `data/`, các file mẫu, dataset/metrics và báo cáo Markdown được giữ.
  Không gỡ ứng dụng hoặc xóa file ngoài workspace, không chạy Docker system prune.

Các XML được tham chiếu trong báo cáo lịch sử đã được dọn; số liệu tổng kết vẫn được
giữ trong các báo cáo Markdown. Các lệnh Python native cũ được thay bằng lệnh Docker
trong README hiện tại.
