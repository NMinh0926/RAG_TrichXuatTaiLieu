import { useState } from 'react';
import LoadingIndicator from '../components/LoadingIndicator.jsx';
import SourceCard from '../components/SourceCard.jsx';
import { askQuestion } from '../services/api.js';

export default function AskPage() {
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    if (!question.trim() || busy) return;
    setBusy(true); setResult(null); setError('');
    try { setResult(await askQuestion(question.trim())); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  return <>
    <div className="page-heading"><p className="eyebrow">TÌM CÂU TRẢ LỜI CÓ NGUỒN</p>
      <h1>Hỏi đáp tài liệu</h1><p>Đặt câu hỏi dựa trên các tài liệu đã được nạp vào hệ thống.</p></div>
    <section className="panel" aria-label="Đặt câu hỏi">
      <form onSubmit={submit} aria-busy={busy}>
        <label htmlFor="question">Câu hỏi của bạn</label>
        <textarea id="question" rows="4" maxLength={4000} required disabled={busy}
          placeholder="Ví dụ: Một năm người lao động có bao nhiêu ngày phép?"
          value={question} onChange={event => { setQuestion(event.target.value); setResult(null); setError(''); }} />
        <div className="form-actions"><p className="hint">Hỏi ngắn gọn, một vấn đề mỗi lần.</p>
          <button type="submit" disabled={busy || !question.trim()}>{busy ? 'Đang tìm câu trả lời…' : 'Hỏi'}</button></div>
      </form>
      {busy && <LoadingIndicator>Đang tìm thông tin trong tài liệu và tạo câu trả lời…</LoadingIndicator>}
      {error && <div className="message error" role="alert">{error}</div>}
    </section>
    {result && <section className="panel answer-panel" aria-live="polite" aria-label="Kết quả trả lời">
      <h2>Câu trả lời</h2><p className="answer">{result.answer}</p>
      {result.sources?.length > 0 && <div className="sources"><h3>Nguồn tài liệu</h3>
        <ul>{result.sources.map((source, i) => <SourceCard key={i} source={source} />)}</ul></div>}
    </section>}
    <p className="page-note">Mỗi lần hỏi là một yêu cầu riêng. Câu trả lời trước không được dùng làm ngữ cảnh.</p>
  </>;
}
