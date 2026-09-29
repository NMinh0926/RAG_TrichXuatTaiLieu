import { useState } from 'react';
import { Link } from 'react-router-dom';
import LoadingIndicator from '../components/LoadingIndicator.jsx';
import { uploadDocument } from '../services/api.js';

export default function DocumentsPage() {
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  function selectFile(event) {
    setFile(event.target.files?.[0] || null);
    setResult(null);
    setError('');
  }

  async function submit(event) {
    event.preventDefault();
    if (!file || busy) return;
    setBusy(true); setError(''); setResult(null);
    try { setResult(await uploadDocument(file)); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  return <>
    <div className="page-heading"><p className="eyebrow">TÀI LIỆU CỦA BẠN</p>
      <h1>Nạp tài liệu</h1><p>Hỗ trợ PDF, DOCX, XLS/XLSX, JPG, JPEG, PNG và PDF scan.</p></div>
    <section className="panel" aria-label="Nạp tài liệu">
      <form onSubmit={submit} aria-busy={busy}>
        <div className="upload-box"><span className="upload-icon" aria-hidden="true">↑</span>
          <label htmlFor="document-file">Chọn tài liệu để bắt đầu</label>
          <p>Văn bản, bảng tính hoặc tài liệu scan</p>
          <input id="document-file" type="file" accept=".pdf,.docx,.xls,.xlsx,.jpg,.jpeg,.png"
            onChange={selectFile} disabled={busy} required />
        </div>
        {file && <p className="selected-file">Đã chọn: <strong>{file.name}</strong></p>}
        <div className="form-actions"><p className="hint">Tài liệu sẽ được xử lý trước khi bạn có thể đặt câu hỏi.</p>
          <button disabled={!file || busy} type="submit">{busy ? 'Đang xử lý…' : 'Nạp tài liệu'}</button></div>
      </form>
      {busy && <LoadingIndicator>Đang đọc tài liệu và tạo chỉ mục. OCR có thể mất một lúc.</LoadingIndicator>}
      {error && <div className="message error" role="alert">{error}</div>}
      {result && <div className="message success" role="status">
        <h2>Đã xử lý thành công</h2><p className="filename">{result.filename}</p>
        <p>Số đoạn đã tạo (chunks_created): <strong>{result.chunks_created}</strong></p>
        <Link to="/ask">Hỏi về tài liệu →</Link>
      </div>}
    </section>
    <p className="page-note">Kết quả nạp chỉ hiển thị trong phiên hiện tại. Tài liệu đã nạp được dùng để truy xuất câu trả lời.</p>
  </>;
}
