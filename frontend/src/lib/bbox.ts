import type { Bbox } from '../api/types';

/** Rectangle in CSS pixels relative to the top-left corner of the rendered page. */
export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Converts a bbox in PDF points (top-left origin, unrotated page) to CSS pixels at `scale` px per point. */
export function scaleBbox(bbox: Bbox, scale: number): Rect {
  const left = Math.min(bbox.x0, bbox.x1);
  const top = Math.min(bbox.y0, bbox.y1);
  return {
    left: left * scale,
    top: top * scale,
    width: Math.abs(bbox.x1 - bbox.x0) * scale,
    height: Math.abs(bbox.y1 - bbox.y0) * scale,
  };
}

/** Vertical offset (CSS px, from the page top) of the first highlight on `page`, or null when there is none. */
export function firstHighlightTop(bboxes: readonly Bbox[], page: number, scale: number): number | null {
  const tops = bboxes.filter((bbox) => bbox.page === page).map((bbox) => Math.min(bbox.y0, bbox.y1) * scale);
  return tops.length ? Math.min(...tops) : null;
}

export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 4;

export function clampZoom(scale: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));
}

/** Scale at which a page `pageWidth` points wide fills `availableWidth` CSS pixels. */
export function fitWidthScale(availableWidth: number, pageWidth: number): number {
  return pageWidth > 0 && availableWidth > 0 ? clampZoom(availableWidth / pageWidth) : 1;
}

// Upper bound for one page canvas (width x height in device pixels) to keep memory bounded when zoomed in.
const MAX_CANVAS_PIXELS = 16_000_000;

/** Device pixels per CSS pixel for a page canvas: the screen's ratio, reduced when the canvas would be too large. */
export function canvasPixelRatio(cssWidth: number, cssHeight: number, devicePixelRatio: number): number {
  const area = cssWidth * cssHeight;
  if (area <= 0) return devicePixelRatio;
  return Math.min(devicePixelRatio, Math.sqrt(MAX_CANVAS_PIXELS / area));
}

/** Top offset (CSS px) of each page when pages of `heights` points are stacked at `scale` with `padding` and `gap`. */
export function pageTops(heights: readonly number[], scale: number, padding: number, gap: number): number[] {
  const tops: number[] = [];
  let y = padding;
  for (const height of heights) {
    tops.push(y);
    y += height * scale + gap;
  }
  return tops;
}

/** 1-based number of the page whose area contains the offset `y`. */
export function pageAtOffset(tops: readonly number[], y: number): number {
  let page = 1;
  tops.forEach((top, i) => {
    if (top <= y) page = i + 1;
  });
  return page;
}
