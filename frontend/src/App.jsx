import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import DocumentsPage from './pages/DocumentsPage.jsx';
import AskPage from './pages/AskPage.jsx';

export default function App() {
  return <>
    <header className="header">
      <div className="brand"><span className="brand-mark" aria-hidden="true">R</span>Multi-modal RAG Assistant</div>
      <nav aria-label="Điều hướng chính">
        <NavLink to="/documents">Nạp tài liệu</NavLink>
        <NavLink to="/ask">Hỏi tài liệu</NavLink>
      </nav>
    </header>
    <main><Routes>
      <Route path="/documents" element={<DocumentsPage />} />
      <Route path="/ask" element={<AskPage />} />
      <Route path="*" element={<Navigate to="/documents" replace />} />
    </Routes></main>
    <footer>Trả lời từ tài liệu của bạn · Kiểm tra nguồn để đối chiếu thông tin.</footer>
  </>;
}
