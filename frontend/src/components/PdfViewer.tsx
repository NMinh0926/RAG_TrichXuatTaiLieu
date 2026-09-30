// Legacy build: pdf.js 6 relies on very recent JavaScript (e.g. Map.prototype.getOrInsertComputed) that
// current office browsers lack; the legacy build ships the polyfills, for both the page and the worker.
import {
  getDocument,
  GlobalWorkerOptions,
  RenderingCancelledException,
  type PDFDocumentProxy,
  type RenderTask,
} from 'pdfjs-dist/legacy/build/pdf.mjs';
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url';
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import type { Bbox } from '../api/types';
import { canvasPixelRatio, clampZoom, firstHighlightTop, fitWidthScale, pageAtOffset, pageTops, scaleBbox } from '../lib/bbox';
import { Icon } from './Icon';
import { ErrorBanner, iconButton, Skeleton } from './ui';

// The worker is bundled by Vite and served from our own origin (no CDN).
GlobalWorkerOptions.workerSrc = workerUrl;

const PADDING = 16;
const GAP = 12;
// Pages this far outside the visible area are rendered ahead of scrolling.
const OVERSCAN = 800;
const ZOOM_STEP = 1.25;
const NO_HIGHLIGHTS: Bbox[] = [];

export interface PdfTarget {
  page: number | null;
  bboxes: Bbox[];
  nonce: number;
}

interface PageSize {
  width: number;
  height: number;
}

interface PdfPageProps {
  pdf: PDFDocumentProxy;
  number: number;
  size: PageSize;
  scale: number;
  top: number;
  visible: boolean;
  highlights: Bbox[];
}

const PdfPage = memo(function PdfPage({ pdf, number, size, scale, top, visible, highlights }: PdfPageProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  // The previous render of this canvas. pdf.js refuses a second render() on a canvas until the first has
  // finished or its cancellation has settled, so renders (e.g. after a zoom change) are chained.
  const previous = useRef<Promise<void>>(Promise.resolve());
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    let task: RenderTask | null = null;
    let cancelled = false;
    const run = previous.current.then(async () => {
      if (cancelled) return;
      if (!visible) {
        // Free the bitmap of pages far from the viewport.
        element.width = 0;
        element.height = 0;
        return;
      }
      const page = await pdf.getPage(number);
      if (cancelled) return;
      const viewport = page.getViewport({ scale });
      const ratio = canvasPixelRatio(viewport.width, viewport.height, window.devicePixelRatio || 1);
      element.width = Math.floor(viewport.width * ratio);
      element.height = Math.floor(viewport.height * ratio);
      task = page.render({ canvas: element, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
      await task.promise;
      setFailed(false);
    });
    previous.current = run.catch((error: unknown) => {
      if (error instanceof RenderingCancelledException) return;
      console.error(`PDF page ${number} failed to render`, error);
      setFailed(true);
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, number, scale, visible]);

  const width = size.width * scale;
  const height = size.height * scale;
  return (
    <div
      role="img"
      aria-label={`Trang ${number}`}
      className="absolute left-1/2 -translate-x-1/2 overflow-hidden bg-white shadow-md ring-1 ring-slate-900/10"
      style={{ top, width, height }}
    >
      <canvas ref={canvas} className="block size-full" />
      {failed && <p className="absolute inset-0 flex items-center justify-center text-sm text-red-700">Không hiển thị được trang {number}.</p>}
      {highlights.map((bbox, i) => {
        const rect = scaleBbox(bbox, scale);
        return <div key={i} className="pdf-highlight" style={rect} />;
      })}
    </div>
  );
});

/** PDF.js viewer: stacked pages rendered lazily near the viewport, zoom, page navigation and citation highlights. */
export default function PdfViewer({ url, target }: { url: string; target: PdfTarget }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [sizes, setSizes] = useState<PageSize[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState<number | 'fit'>('fit');
  const [box, setBox] = useState({ width: 0, height: 0 });
  const [scrollTop, setScrollTop] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  const appliedNonce = useRef<number | null>(null);
  const previousScale = useRef<number | null>(null);

  useEffect(() => {
    setPdf(null);
    setSizes([]);
    setError(null);
    appliedNonce.current = null;
    let cancelled = false;
    const task = getDocument({ url });
    task.promise
      .then(async (document) => {
        const pages = await Promise.all(Array.from({ length: document.numPages }, (_, i) => document.getPage(i + 1)));
        if (cancelled) return;
        setSizes(
          pages.map((page) => {
            const viewport = page.getViewport({ scale: 1 });
            return { width: viewport.width, height: viewport.height };
          }),
        );
        setPdf(document);
      })
      .catch(() => {
        if (!cancelled) setError('Không mở được file PDF.');
      });
    return () => {
      cancelled = true;
      void task.destroy();
    };
  }, [url]);

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setBox({ width: element.clientWidth, height: element.clientHeight }));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const widest = sizes.reduce((max, size) => Math.max(max, size.width), 0);
  const scale = zoom === 'fit' ? fitWidthScale(box.width - 2 * PADDING, widest) : zoom;
  const tops = useMemo(() => pageTops(sizes.map((size) => size.height), scale, PADDING, GAP), [sizes, scale]);
  const lastTop = tops.at(-1);
  const lastSize = sizes.at(-1);
  const contentHeight = lastTop !== undefined && lastSize ? lastTop + lastSize.height * scale + PADDING : 0;
  const currentPage = sizes.length ? pageAtOffset(tops, scrollTop + box.height * 0.3) : 0;
  const highlightsByPage = useMemo(() => {
    const byPage = new Map<number, Bbox[]>();
    for (const bbox of target.bboxes) byPage.set(bbox.page, [...(byPage.get(bbox.page) ?? []), bbox]);
    return byPage;
  }, [target.bboxes]);

  // Keep the same part of the document in view when the scale changes.
  useLayoutEffect(() => {
    const element = scroller.current;
    const previous = previousScale.current;
    previousScale.current = scale;
    if (element && previous && previous !== scale) element.scrollTop *= scale / previous;
  }, [scale]);

  // Scroll to a new citation target once the document is laid out.
  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element || !sizes.length || !box.width || appliedNonce.current === target.nonce) return;
    appliedNonce.current = target.nonce;
    const wanted = target.page ?? target.bboxes[0]?.page ?? 1;
    const page = Math.min(Math.max(1, wanted), sizes.length);
    const highlightTop = firstHighlightTop(target.bboxes, page, scale);
    const pageTop = tops[page - 1] ?? 0;
    element.scrollTop = highlightTop === null ? pageTop - PADDING : pageTop + highlightTop - element.clientHeight / 3;
    setScrollTop(element.scrollTop);
  }, [target, sizes, box.width, scale, tops]);

  const goToPage = useCallback(
    (page: number) => {
      const element = scroller.current;
      const top = tops[Math.min(Math.max(1, page), tops.length) - 1];
      if (element && top !== undefined) element.scrollTop = top - PADDING;
    },
    [tops],
  );

  const onPageSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = Number(new FormData(event.currentTarget).get('page'));
    if (Number.isInteger(value)) goToPage(value);
  };

  const zoomBy = (factor: number) => setZoom(clampZoom(scale * factor));

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div role="toolbar" aria-label="Điều khiển trình xem PDF" className="flex flex-wrap items-center gap-1 border-b border-slate-200 px-2 py-1.5 text-sm dark:border-slate-800">
        <button type="button" className={iconButton} onClick={() => goToPage(currentPage - 1)} disabled={currentPage <= 1} aria-label="Trang trước">
          <Icon name="chevronLeft" />
        </button>
        <form onSubmit={onPageSubmit} className="flex items-center gap-1">
          <input
            key={currentPage}
            name="page"
            type="number"
            min={1}
            max={sizes.length || 1}
            defaultValue={currentPage || ''}
            disabled={!sizes.length}
            aria-label="Số trang hiện tại"
            className="w-14 rounded-md border border-slate-300 bg-transparent px-1.5 py-1 text-center dark:border-slate-700"
          />
          <span className="text-slate-500 dark:text-slate-400">/ {sizes.length || '–'}</span>
        </form>
        <button type="button" className={iconButton} onClick={() => goToPage(currentPage + 1)} disabled={!sizes.length || currentPage >= sizes.length} aria-label="Trang sau">
          <Icon name="chevronRight" />
        </button>
        <span className="mx-1 h-5 w-px bg-slate-200 dark:bg-slate-700" aria-hidden="true" />
        <button type="button" className={iconButton} onClick={() => zoomBy(1 / ZOOM_STEP)} disabled={!sizes.length} aria-label="Thu nhỏ">
          <Icon name="zoomOut" />
        </button>
        <span className="w-12 text-center tabular-nums" aria-live="polite" aria-label="Mức thu phóng">
          {Math.round(scale * 100)}%
        </span>
        <button type="button" className={iconButton} onClick={() => zoomBy(ZOOM_STEP)} disabled={!sizes.length} aria-label="Phóng to">
          <Icon name="zoomIn" />
        </button>
        <button
          type="button"
          className={`${iconButton} ${zoom === 'fit' ? 'bg-slate-200 dark:bg-slate-800' : ''}`}
          onClick={() => setZoom('fit')}
          disabled={!sizes.length}
          aria-pressed={zoom === 'fit'}
          aria-label="Vừa chiều rộng"
          title="Vừa chiều rộng"
        >
          <Icon name="fitWidth" />
        </button>
      </div>
      <div
        ref={scroller}
        tabIndex={0}
        aria-label="Nội dung PDF"
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        className="relative min-h-0 flex-1 overflow-auto bg-slate-200 dark:bg-slate-800"
      >
        {error ? (
          <div className="p-4">
            <ErrorBanner message={error} />
          </div>
        ) : !pdf ? (
          <div role="status" aria-label="Đang tải PDF" className="mx-auto max-w-xl space-y-3 p-4">
            <Skeleton className="aspect-[1/1.3] w-full" />
          </div>
        ) : (
          <div className="relative" style={{ height: contentHeight, width: widest * scale + 2 * PADDING, minWidth: '100%' }}>
            {sizes.map((size, i) => {
              const top = tops[i] ?? 0;
              const visible = top < scrollTop + box.height + OVERSCAN && top + size.height * scale > scrollTop - OVERSCAN;
              return (
                <PdfPage
                  key={i}
                  pdf={pdf}
                  number={i + 1}
                  size={size}
                  scale={scale}
                  top={top}
                  visible={visible}
                  highlights={highlightsByPage.get(i + 1) ?? NO_HIGHLIGHTS}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
