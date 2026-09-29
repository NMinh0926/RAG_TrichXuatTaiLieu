/** Citation markers in answers: [1], [2][3], [1, 2], [1-3]. */

export interface Citation {
  index: number;
  length: number;
  numbers: number[];
}

const CITATION = /\[(\d+(?:\s*[,\-–]\s*\d+)*)\]/g;
const CODE = /(```[\s\S]*?(?:```|$)|`[^`\n]*`)/;
const HREF_PREFIX = '#cite-';
// Guards against expanding absurd ranges such as [1-100000].
const MAX_RANGE = 50;

/** Expands the inside of a marker ("1, 3-4") to source numbers, or null when any part is not a valid source. */
export function citationNumbers(inner: string, sourceCount: number): number[] | null {
  const numbers: number[] = [];
  for (const part of inner.split(',')) {
    const bounds = part.split(/[-–]/).map((value) => Number(value.trim()));
    const first = bounds[0] ?? 0;
    const last = bounds[1] ?? first;
    if (bounds.length > 2 || first < 1 || last < first || last > sourceCount) return null;
    if (last - first > MAX_RANGE) return null;
    for (let n = first; n <= last; n += 1) if (!numbers.includes(n)) numbers.push(n);
  }
  return numbers;
}

/** Finds citation markers that refer to existing sources (1..sourceCount); other bracketed numbers are ignored. */
export function findCitations(text: string, sourceCount: number): Citation[] {
  const citations: Citation[] = [];
  for (const match of text.matchAll(CITATION)) {
    const numbers = citationNumbers(match[1] ?? '', sourceCount);
    if (numbers) citations.push({ index: match.index, length: match[0].length, numbers });
  }
  return citations;
}

export function citationHref(n: number): string {
  return `${HREF_PREFIX}${n}`;
}

export function parseCitationHref(href: string | undefined): number | null {
  if (!href?.startsWith(HREF_PREFIX)) return null;
  const n = Number(href.slice(HREF_PREFIX.length));
  return Number.isInteger(n) && n > 0 ? n : null;
}

/** Rewrites citation markers outside code as markdown links `[n](#cite-n)` so they can render as chips. */
export function linkifyCitations(markdown: string, sourceCount: number): string {
  if (sourceCount < 1) return markdown;
  return markdown
    .split(CODE)
    .map((part, i) => {
      if (i % 2 === 1) return part; // Code span or fence: keep literal.
      let result = '';
      let cursor = 0;
      for (const { index, length, numbers } of findCitations(part, sourceCount)) {
        result += part.slice(cursor, index) + numbers.map((n) => `[${n}](${citationHref(n)})`).join('');
        cursor = index + length;
      }
      return result + part.slice(cursor);
    })
    .join('');
}
