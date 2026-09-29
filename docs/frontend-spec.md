# Frontend specification (Stage 4)

Stack (approved): **React 19 + TypeScript + Vite + Tailwind CSS**, PDF.js (`pdfjs-dist`) for the viewer,
`react-markdown` + `remark-gfm` for answers. UI text in Vietnamese. Runs in docker compose as an nginx
container that serves the built app and reverse-proxies `/api` and `/health` to the backend (same origin:
no CORS, SSE must not be buffered: `proxy_buffering off`).

## Backend API (base = same origin)

All errors are JSON `{"error": "<code>", "detail": "<Vietnamese message>"}` (validation errors: FastAPI
`{"detail": [...]}`). Show `detail` to the user.

### Documents and jobs
- `GET /api/documents?page=1&page_size=20&q=<name search>` → `{items: Document[], total, page, page_size}`
- `Document`: `{id, filename, content_type, size_bytes, sha256, page_count|null, chunk_count,
  status: "pending"|"processing"|"ready"|"failed", error_message|null, created_at, updated_at,
  latest_job: Job|null}`
- `POST /api/documents` (multipart field `file`; PDF, DOCX, XLS/XLSX, PNG, JPG/JPEG; max size from server:
  413 when too large, 415 unsupported) → **202** `{document, job, duplicate:false}` or **200**
  `{document, job, duplicate:true}` (identical file already stored; show "đã có sẵn").
- `Job`: `{id, document_id, status: "queued"|"running"|"succeeded"|"failed", progress: 0-100,
  step: "queued"|"download"|"extract"|"chunk"|"embed"|"index"|"done", error_code|null, error_message|null,
  warnings: [{page, step, error}]|null, created_at, started_at|null, finished_at|null}`
- `GET /api/jobs/{id}` → `Job`
- `GET /api/jobs/{id}/events` → Server-Sent Events (GET, EventSource works): `event: progress` with a `Job`
  whenever step/progress changes, then `event: done` with the final `Job` (status succeeded/failed), or
  `event: error` `{error}`. Stream ends after `done`.
- `GET /api/documents/{id}` → `Document`; `GET /api/documents/{id}/file` → original file (inline).
- `DELETE /api/documents/{id}` → 204; **409** while pending/processing.
- `POST /api/documents/{id}/reindex` → 202 `Job`.

Step labels (Vietnamese): queued "Đang chờ", download "Đang tải file", extract "Đang đọc/OCR",
chunk "Đang tách đoạn", embed "Đang tạo vector", index "Đang lưu chỉ mục", done "Hoàn tất".

### Chat
- `POST /api/chat` body `{question: string (1-4000), conversation_id?: uuid, document_ids?: uuid[]}` →
  `text/event-stream` over **POST** (use `fetch` + `ReadableStream`, not EventSource). Before the stream
  starts, 404/422 JSON errors are possible. Events (`event: <kind>\ndata: <json>\n\n`):
  - `meta` `{conversation_id, user_message_id}` (first), then `meta` `{intent: "qa"|"summary", question}`
    (`question` is the follow-up rewritten as a standalone question).
  - `progress` (summaries only) `{stage: "map"|"merge"|"reduce"|"cached"|"combine", done?, total?, document?}`
    → show "Đang tóm tắt phần 2/5 của <tên file>…".
  - `token` `{text}` → append to the answer (markdown).
  - `replace` `{text}` → replace the whole streamed answer (model refused; canonical sentence).
  - `done` `{message_id, answer, sources: Source[], intent}` → final answer text and sources.
  - `error` `{error, detail, status}` → show `detail` (e.g. LLM not running), answer is not stored.
- `Source`: `{n, chunk_id|null, document_id|null, filename, page|null, sheet|null, excerpt|null,
  bboxes: [{page, x0, y0, x1, y1}]|null}`. Coordinates are **PDF points** (1/72 in), origin **top-left**
  of the page (PyMuPDF convention). Answer text cites sources as `[n]` (also `[2][3]`, `[1, 2]`, `[1-3]`).
  Summary answers have document-level sources (`chunk_id` null, `page` null) → open the document.
- `GET /api/conversations?page&page_size` → `{items: [{id, title, created_at, updated_at}], total, ...}`
  (newest updated first). `GET /api/conversations/{id}` → `{id, title, created_at, updated_at,
  messages: [{id, role: "user"|"assistant", content, sources: Source[]|null, created_at}]}`.
  `DELETE /api/conversations/{id}` → 204.

### Health
- `GET /health` (API up), `GET /health/llm` (`{status, model, model_available}` or 503 JSON error).

## Screens and behavior

1. **Layout**: left sidebar (tabs or sections: document library + conversation history), chat in the
   center, PDF viewer panel on the right that can be opened/closed. Mobile: sidebar and viewer become
   drawers/overlays; chat is full width.
2. **Document library**: drag & drop (and file picker) of **multiple** files; each upload shows a live
   progress bar from `/api/jobs/{id}/events` with the step label; duplicate → notice; failure → error with
   `error_message`; per-page OCR warnings shown as a small warning. List shows name, size (KB/MB), pages,
   upload date (vi-VN), status badge; search box (debounced, `q`), pagination or "load more"; delete with a
   confirmation dialog (409 → show message). Clicking a ready PDF opens it in the viewer; images open as an
   image; other types show metadata + download link.
3. **Chat**: streaming answer rendered as markdown (GFM tables/lists), copy button per answer, stop/disable
   input while streaming, quick prompt buttons "Tóm tắt tài liệu", "Liệt kê ý chính". **Scope selector**:
   all documents / selected documents (multi-select from ready documents) → `document_ids`. New chat
   button; conversation history list in the sidebar (click to load messages; delete). Summary progress
   events shown as a status line.
4. **Citations**: every `[n]` in an answer is a clickable chip; clicking opens the source PDF in the viewer,
   goes to the page, scrolls to and highlights the `bboxes` rectangles (scale points × viewport scale;
   rotation-free pages). A source list under each answer shows filename, page, excerpt; clicking it does
   the same. Sources without bboxes open the page without highlight.
5. **PDF viewer**: PDF.js canvas rendering with page navigation, zoom in/out/fit width, current page
   indicator; lazy render of visible pages; loads `/api/documents/{id}/file`. The pdf.js worker must be
   bundled (Vite `?url` import), not loaded from a CDN.
6. **Quality**: responsive, dark/light mode toggle (remember choice in localStorage, default system),
   loading skeletons, empty states ("Chưa có tài liệu", "Hãy đặt câu hỏi…"), error banners, accessible
   labels/roles, keyboard submit (Enter sends, Shift+Enter newline). All UI text Vietnamese.
7. **Code**: TypeScript strict, one API client module (`src/api/`) with typed functions and a single
   `request()` wrapper (so auth headers can be added later), SSE parser utility with unit tests (Vitest),
   components under `src/components/`, no dead code, no secrets. `npm run build`, `npm run test`,
   `npm run typecheck` (tsc --noEmit) must pass.
8. **Docker**: `frontend/Dockerfile` multi-stage (node:22-alpine build → nginx:alpine serve) with
   `frontend/nginx.conf` (SPA fallback, `/api` and `/health` proxied to `http://api:8000`, SSE-friendly
   settings, `client_max_body_size` ≥ 100m). compose service `frontend` on `127.0.0.1:8080:80`.
   Dev: `npm run dev` with Vite proxy to `http://127.0.0.1:8000`.
