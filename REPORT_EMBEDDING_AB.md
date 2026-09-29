# A/B embedding retrieval — 29/09/2026

**Kết luận: chưa đề xuất đổi production ngay.** BGE-M3 cải thiện rõ bộ 25 câu và
phân biệt câu “bài toán 1”/“bài toán 3” tốt hơn MiniLM. Tuy nhiên threshold được
hiệu chỉnh trên 25 câu vẫn loại hai câu đúng trên PDF thực tế. E5-base nhẹ hơn BGE
nhưng không giải quyết được vấn đề này. Không model nào có một threshold chung
giữ toàn bộ các câu đúng và loại toàn bộ no-answer trong cả hai bộ đã đo.

Production vẫn dùng MiniLM, threshold 0.55 và Chroma cũ. Đã đối chiếu SHA256:
`.env`, config, embedding service, chunking, query dataset và cả 6 file của Chroma
production đều **không đổi**. Backend `/health` vẫn OK. Không rebuild/restart backend
hoặc re-embed production trong thí nghiệm này.

## 1. Current embedding baseline

Model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.
Revision: `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`.

- Dataset gốc: 25 câu, 21 answerable, 4 no-answer; không sửa câu hỏi/kỳ vọng/corpus.
- Threshold baseline **0.55**, Top-K **4**, cosine relevance = 1 − distance.
- Hit **19/21 (90.48%)**; no-answer false positive **0/4**.
- Không threshold: cả 21 câu đều có chunk đúng trong raw Top-K; hai miss là do filter.
- Nova Phone: **0.502113**; Vega extension: **0.530722**.
- Supplemental PDF: “bài toán 1” đúng **0.305914**, “bài toán 2” chunk đúng
  **0.234000**, “bài toán 3” không có đáp án **0.322442**.

Điểm từng câu và raw Top-K: [current.json](evaluation/embedding_ab/current.json).

## 2. New model result

Đã thử đúng **hai model mới**, tuần tự, không thử thêm model nhỏ/large khác:

1. **BAAI/bge-m3**, revision `5617a9f61b028005a4858fdac845db406aefb181`:
   dense embedding 1024 chiều, không query instruction, normalize vectors.
2. **intfloat/multilingual-e5-base**, revision `d128750597153bb5987e10b1c3493a34e5a4502a`:
   dense embedding 768 chiều, document prefix `passage: `, query prefix `query: `,
   bao gồm tiếng Việt. Thử dự phòng vì BGE encode chunks chậm hơn MiniLM 8.6 lần.

Cả hai dùng SentenceTransformers theo model card chính thức; không dùng sparse/
ColBERT/reranker/hybrid retrieval. [BGE-M3 usage](https://huggingface.co/BAAI/bge-m3),
[E5 usage](https://huggingface.co/intfloat/multilingual-e5-base).

Kết quả đầy đủ: [bge-m3.json](evaluation/embedding_ab/bge-m3.json),
[e5-base.json](evaluation/embedding_ab/e5-base.json).

## 3. A/B comparison

### Giữ nguyên threshold 0.55 trước khi tune

| Metric trên 25 câu | Current MiniLM | BGE-M3 | E5-base |
|---|---:|---:|---:|
| Answerable hit | 19/21 | 21/21 | 21/21 |
| Retrieval hit rate | 90.48% | 100% | 100% |
| No-answer false positive | 0/4 | 2/4 | 4/4 |

Không lấy 100% hit ở threshold cũ làm bằng chứng đủ để đổi model: thang điểm khác
nhau và false positive tăng. E5 có cosine scores tập trung ở vùng cao theo đặc tính
training được mô tả trong [model card](https://huggingface.co/intfloat/multilingual-e5-base#faq).

### Sau calibration riêng trên cùng 25 câu

| Metric | Current MiniLM | BGE-M3 | E5-base |
|---|---:|---:|---:|
| Threshold thử nghiệm | **0.55** | **0.57** | **0.84** |
| Answerable hit | 19/21 | **21/21** | 20/21 |
| Retrieval hit rate | 90.48% | **100%** | 95.24% |
| No-answer false positive | **0/4** | **0/4** | **0/4** |
| Nova Phone | Miss | Hit | Hit |
| Vega extension | Miss | Hit | Hit |
| “bài toán 3 là gì” — supplemental | Reject | Reject | Reject |
| Hai câu đúng “bài toán 1/2” — supplemental | 0/2 | **0/2** | **0/2** |

E5 chuyển miss sang câu nghỉ phép tiếng Việt (id 6), điểm **0.819654 < 0.84**.
Không tính 3 câu supplemental vào mẫu số 21 hoặc sửa dataset để tăng điểm.

### Kiểm soát công bằng

- Chạy extraction/OCR/cleaning/chunking một lần bằng pipeline/tokenizer MiniLM hiện tại.
- Đóng băng **7 chunk evaluation**, **6 chunk PDF**; mọi model nhận cùng text/metadata.
- Chunks, query set và Top-K cùng fingerprint giữa ba lượt.
- Original questions SHA256: `760cc317ab4ae1545d696305f9111fbdbf80ce3b64feaa00cf6aa5f8217d3de9`.
- Frozen input SHA256: `e96aee8378781e78facab6552b9d8c1664366978c47f202450a09b126686ed47`.
- DB hoàn toàn riêng dưới `data/chroma_embedding_test/ab_20260929/databases/`;
  mỗi model/corpus/lượt có thư mục và collection riêng.
- Workspace/production được mount read-only. Snapshot có text PDF nằm trong `data/`
  bị loại khỏi submission; artifact chia sẻ chỉ có scores/ranks/metadata, không text.

**Lưu ý kỹ thuật:** một chunk PDF của baseline tokenize lại thành 129 token,
vượt giới hạn MiniLM 128 đúng 1 token. Giữ nguyên hành vi truncate hiện có để không
thay baseline/chunking. Hai model mới không truncate bất kỳ chunk cố định nào.
Bộ evaluation 25 câu không có chunk vượt giới hạn; vì vậy cải thiện 19→21 trên
bộ này không đến từ việc sửa chunking hoặc khôi phục token bị truncate.

## 4. Similarity của các câu quan trọng

Đối với câu đúng, bảng dùng **score của chunk đúng cao nhất trong Top-K**; đối với
no-answer, dùng score cao nhất của bất kỳ candidate nào.

| Câu hỏi | Current | BGE-M3 | E5-base |
|---|---:|---:|---:|
| Nova Phone revenue | 0.502113 | **0.649900** | 0.852412 |
| Vega emergency extension | 0.530722 | **0.583019** | 0.856612 |
| “bài toán 3 là gì” — no-answer | 0.322442 | 0.480021 | 0.816656 |
| “bài toán 1 là gì” — đúng | 0.305914 | **0.517536** | 0.810567 |
| “bài toán 2 là gì” — đúng | 0.234000 | **0.536601** | 0.819681 |

E5 với “bài toán 1” có top candidate **0.823224**, nhưng candidate đó không khớp
fact kỳ vọng; chunk đúng chỉ **0.810567**. Không dùng top score sai để báo đã cải thiện.

BGE đảo được quan hệ đúng/sai của câu hỏi ngắn: đúng 1/2 đều cao hơn sai 3. Điều
này cho thấy embedding là một yếu tố thực sự gây lỗi, không phải Chroma mất dữ liệu.
Tuy nhiên điểm đúng của PDF vẫn thấp hơn một số no-answer trong corpus evaluation.

## 5. Relevant vs no-answer similarity distribution

Trên 25 câu gốc, relevant = best relevant score của 21 câu; no-answer = top score
của 4 câu không có đáp án. Cosine score không phải xác suất trả lời đúng.

| Model / nhóm | Min | P25 | Median | P75 | Max |
|---|---:|---:|---:|---:|---:|
| MiniLM relevant | 0.5021 | 0.6514 | 0.6967 | 0.7391 | 0.8294 |
| MiniLM no-answer | 0.1738 | 0.2156 | 0.2459 | 0.3299 | 0.5329 |
| BGE relevant | 0.5830 | 0.7004 | 0.7317 | 0.7454 | 0.7709 |
| BGE no-answer | 0.3358 | 0.3397 | 0.4469 | 0.5548 | 0.5606 |
| E5 relevant | 0.8197 | 0.8640 | 0.8843 | 0.8928 | 0.9086 |
| E5 no-answer | 0.7102 | 0.7593 | 0.8015 | 0.8291 | 0.8343 |

Gap = min relevant − max no-answer:

| Phạm vi | MiniLM | BGE-M3 | E5-base |
|---|---:|---:|---:|
| Evaluation 25 câu | −0.0308 | **+0.0224** | −0.0146 |
| Supplemental 3 câu | −0.0884 | **+0.0375** | −0.0061 |
| Kết hợp hai bộ, chỉ để phân tích | −0.2989 | **−0.0431** | −0.0237 |

BGE tạo được khoảng cách trên từng bộ riêng, nhưng khoảng cách còn nhỏ. Khi gộp
phạm vi câu hỏi, hai phân phối vẫn overlap; chưa có một ngưỡng chung hoàn hảo.
Không chọn threshold riêng theo câu hỏi hoặc tài liệu để làm metric đẹp hơn.

## 6. Recommended threshold cho mỗi model

Sau khi lưu raw results, sweep 0.00→1.00, bước 0.01, trên **25 câu gốc**. Chính sách:
tối đa answerable hits với điều kiện không nhận context ở cả 4 no-answer. Giữ 0.55
của current khi cho cùng chất lượng với lựa chọn mới.

- **MiniLM: 0.55**, giữ nguyên production. Không giải quyết câu hỏi PDF ngắn.
- **BGE-M3: 0.57**, chỉ là ứng viên trên corpus calibration: max negative 0.560602,
  min positive 0.583019. Chưa được áp dụng production.
- **E5-base: 0.84**, zero FP nhưng mất câu nghỉ phép; không đề xuất dùng để thay MiniLM.

Với BGE, cần threshold ≤0.517536 để giữ câu đúng “bài toán 1”; khi đó hai no-answer
evaluation có điểm 0.552808 và 0.560602 cũng vượt ngưỡng. Vì vậy 0.57 không sửa được
ca người dùng, còn hạ xuống 0.51 không giữ được zero-FP của corpus chính.

Đây là calibration trên dataset nhỏ, **không phải holdout validation**. Supplemental
chỉ dùng kiểm tra sau calibration, không được dùng để chọn threshold.

## 7. Resource usage

Docker CPU-only, float32, 4 CPU threads, batch size 4; mỗi model chạy tuần tự trong
process/container riêng giới hạn 5 GiB. Không chạy inference model khác song song.

| Metric | MiniLM | BGE-M3 | E5-base |
|---|---:|---:|---:|
| Dense dimension | 384 | 1024 | 768 |
| Cached model load | 2.67 s | 4.47 s | 2.88 s |
| Download/cache check trong lượt thành công | 0.41 s (cached) | 49.15 s | 25.34 s |
| Peak process RSS | 1.22 GiB | 2.33 GiB | 1.42 GiB |
| Encode 13 chunk — warm median | 0.466 s | 3.998 s | 1.003 s |
| Warm chunk throughput | 27.91/s | 3.25/s | 12.97/s |
| Query encode median | 20.94 ms | 122.56 ms | 42.89 ms |
| Query encode P95 | 31.15 ms | 167.80 ms | 58.75 ms |
| Model snapshot trên disk | 499.6 MB | 2310.4 MB | 1151.4 MB |

RSS gồm Python/framework/model và test Chroma trong process; peak bao gồm tải/load.
Không cộng RAM server production/OCR ở process khác. Cached load không gồm download.
Warm throughput là median 3 lượt cùng 13 chunk; query latency đo encode từng query,
không gồm Chroma/network/LLM. Một lượt benchmark trên máy này, chưa phải load test.

BGE không OOM và vừa giới hạn RAM thử nghiệm, nhưng upload encoding chậm 8.6 lần,
query encoding chậm 5.9 lần so với current. E5 lần lượt chậm khoảng 2.15 và 2.05 lần.

## 8. Recommendation

**Không đổi production trong phiên này và chưa đề xuất migration ngay.**

- BGE là ứng viên tốt hơn nếu chỉ xét chất lượng corpus 25 câu: sửa cả Nova/Vega,
  zero-FP sau calibration và cải thiện thứ tự điểm của câu hỏi ngắn PDF.
- Nhưng nó chưa xử lý được ca thực tế dưới một threshold chung bảo toàn no-answer;
  tốc độ CPU và dung lượng model cũng cao hơn rõ rệt.
- E5 tiết kiệm tài nguyên hơn BGE nhưng còn overlap và xếp hạng sai câu “bài toán 1”;
  không đáng đổi production chỉ để chuyển từ 19/21 lên 20/21 trên bộ calibration.
- Cần một tập kiểm chứng lớn hơn gồm câu hỏi tham chiếu ngắn và câu hỏi thiếu thông
  tin trước khi quyết định migration/điều chỉnh chính sách retrieval. Không tự thêm
  architecture khác hoặc dùng threshold riêng cho từng câu để vượt các ca này.

Dừng tại báo cáo để người dùng duyệt bước tiếp theo. Không thay `.env`, model,
production collection, reindex tài liệu hay cấu hình frontend.

Artifacts để kiểm tra:

- [Điểm từng câu CSV](evaluation/embedding_ab/per_question_scores.csv)
- [So sánh tổng hợp JSON](evaluation/embedding_ab/comparison_summary.json)
- [Phương pháp và lệnh tái lập](evaluation/embedding_ab/README.md)
- [Script A/B](scripts/compare_embeddings.py)

## 9. Gemini synthetic connectivity result

Chỉ gửi context tổng hợp đã được cho phép:

```text
Context: Project Orion has internal code ZXQ-91827.
Question: What is the internal code of Project Orion?
```

**FAIL**: Google trả **HTTP 403**, category **`gemini_project_access_denied`**;
backend map thành 503. Không nhận được câu trả lời, nên không ghi “connectivity
verified” cho generation. Đã dừng sau request tổng hợp này.

Không gửi PDF thật, context retrieval của người dùng hoặc file gốc lên Gemini.
Không gửi nội dung corpus ra dịch vụ embedding: cả ba model encode local; mạng
chỉ dùng tải model chính thức. Không lộ key trong artifacts.

Kết quả: [gemini_synthetic.json](evaluation/embedding_ab/gemini_synthetic.json).
