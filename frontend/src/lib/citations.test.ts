import { describe, expect, it } from 'vitest';
import { citationNumbers, findCitations, linkifyCitations, parseCitationHref } from './citations';

const numbers = (text: string, count: number) => findCitations(text, count).map((c) => c.numbers);

describe('findCitations', () => {
  it('finds single markers with their position', () => {
    expect(findCitations('Lãi suất là 5% [1].', 3)).toEqual([{ index: 15, length: 3, numbers: [1] }]);
  });

  it('finds adjacent markers separately', () => {
    expect(numbers('Theo [2][3].', 3)).toEqual([[2], [3]]);
  });

  it('expands comma lists and ranges', () => {
    expect(numbers('A [1, 2] B [1-3] C [1,3–4]', 4)).toEqual([
      [1, 2],
      [1, 2, 3],
      [1, 3, 4],
    ]);
  });

  it('ignores numbers that are not valid source numbers', () => {
    expect(numbers('Năm [2024] và [0] và [4] nhưng [2]', 3)).toEqual([[2]]);
    expect(numbers('[1-5]', 3)).toEqual([]);
    expect(numbers('[3-1]', 3)).toEqual([]);
  });

  it('ignores non-numeric brackets', () => {
    expect(numbers('[a] [1a] [ ] [1]', 2)).toEqual([[1]]);
  });
});

describe('citationNumbers', () => {
  it('deduplicates repeated numbers', () => {
    expect(citationNumbers('1, 1-2', 2)).toEqual([1, 2]);
  });

  it('rejects malformed ranges', () => {
    expect(citationNumbers('1-2-3', 5)).toBeNull();
  });
});

describe('linkifyCitations', () => {
  it('turns markers into citation links', () => {
    expect(linkifyCitations('X [1-2] Y [3]', 3)).toBe('X [1](#cite-1)[2](#cite-2) Y [3](#cite-3)');
  });

  it('leaves code and unknown numbers untouched', () => {
    const fence = '```';
    expect(linkifyCitations(`\`arr[1]\` năm [2024] [1]\n${fence}\nx[1]\n${fence}`, 2)).toBe(
      `\`arr[1]\` năm [2024] [1](#cite-1)\n${fence}\nx[1]\n${fence}`,
    );
  });

  it('returns the text unchanged when there are no sources', () => {
    expect(linkifyCitations('[1]', 0)).toBe('[1]');
  });
});

describe('parseCitationHref', () => {
  it('reads citation links only', () => {
    expect(parseCitationHref('#cite-12')).toBe(12);
    expect(parseCitationHref('https://example.com')).toBeNull();
    expect(parseCitationHref(undefined)).toBeNull();
  });
});
