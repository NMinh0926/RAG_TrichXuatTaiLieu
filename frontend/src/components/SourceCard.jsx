export default function SourceCard({ source }) {
  return <li className="source-card">
    <span aria-hidden="true" className="source-icon">▤</span>
    <span>{source.filename}
      {source.page != null && ` — Trang ${source.page}`}
      {source.sheet != null && ` — Sheet: ${source.sheet}`}
    </span>
  </li>;
}
