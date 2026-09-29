import { describe, expect, it } from 'vitest';
import { canvasPixelRatio, clampZoom, firstHighlightTop, fitWidthScale, pageAtOffset, pageTops, scaleBbox } from './bbox';

describe('scaleBbox', () => {
  it('scales PDF points by the viewport scale', () => {
    expect(scaleBbox({ page: 1, x0: 72, y0: 100, x1: 172, y1: 112 }, 1.5)).toEqual({
      left: 108,
      top: 150,
      width: 150,
      height: 18,
    });
  });

  it('is the identity at scale 1', () => {
    expect(scaleBbox({ page: 2, x0: 10, y0: 20, x1: 30, y1: 25 }, 1)).toEqual({ left: 10, top: 20, width: 20, height: 5 });
  });

  it('normalises inverted corners', () => {
    expect(scaleBbox({ page: 1, x0: 50, y0: 40, x1: 10, y1: 20 }, 2)).toEqual({ left: 20, top: 40, width: 80, height: 40 });
  });
});

describe('firstHighlightTop', () => {
  const boxes = [
    { page: 1, x0: 0, y0: 300, x1: 10, y1: 310 },
    { page: 2, x0: 0, y0: 200, x1: 10, y1: 210 },
    { page: 2, x0: 0, y0: 120, x1: 10, y1: 130 },
  ];

  it('returns the top of the highest box on the page, scaled', () => {
    expect(firstHighlightTop(boxes, 2, 2)).toBe(240);
  });

  it('returns null when the page has no box', () => {
    expect(firstHighlightTop(boxes, 3, 1)).toBeNull();
  });
});

describe('zoom helpers', () => {
  it('fits the page width into the available width', () => {
    expect(fitWidthScale(918, 612)).toBe(1.5);
    expect(fitWidthScale(0, 612)).toBe(1);
  });

  it('clamps zoom to the supported range', () => {
    expect(clampZoom(10)).toBe(4);
    expect(clampZoom(0.01)).toBe(0.25);
  });
});

describe('canvasPixelRatio', () => {
  it('uses the device pixel ratio for normal pages', () => {
    expect(canvasPixelRatio(918, 1188, 2)).toBe(2);
  });

  it('caps the canvas size for large zoom levels', () => {
    const ratio = canvasPixelRatio(2448, 3168, 2);
    expect(ratio).toBeLessThan(2);
    expect(2448 * ratio * 3168 * ratio).toBeCloseTo(16_000_000, -2);
  });
});

describe('page layout', () => {
  const tops = pageTops([792, 792, 612], 0.5, 16, 12);

  it('stacks scaled pages with padding and gaps', () => {
    expect(tops).toEqual([16, 424, 832]);
  });

  it('finds the page at a scroll offset', () => {
    expect(pageAtOffset(tops, 0)).toBe(1);
    expect(pageAtOffset(tops, 430)).toBe(2);
    expect(pageAtOffset(tops, 5000)).toBe(3);
  });
});
