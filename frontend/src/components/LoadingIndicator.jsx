export default function LoadingIndicator({ children }) {
  return <p className="loading" role="status"><span className="spinner" aria-hidden="true" />{children}</p>;
}
