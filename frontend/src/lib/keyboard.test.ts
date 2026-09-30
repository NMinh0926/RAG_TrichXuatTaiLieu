import { describe, expect, it } from 'vitest';
import { isUnhandledEscape } from './keyboard';

function target(insideDialog: boolean): EventTarget {
  return { closest: (selector: string) => (insideDialog && selector.includes('dialog') ? {} : null) } as unknown as EventTarget;
}

describe('isUnhandledEscape', () => {
  it('accepts a plain Escape press', () => {
    expect(isUnhandledEscape({ key: 'Escape', defaultPrevented: false, target: target(false) })).toBe(true);
    expect(isUnhandledEscape({ key: 'Escape', defaultPrevented: false, target: null })).toBe(true);
  });

  it('ignores Escape already handled by a menu or popover', () => {
    expect(isUnhandledEscape({ key: 'Escape', defaultPrevented: true, target: target(false) })).toBe(false);
  });

  it('ignores Escape inside a dialog and other keys', () => {
    expect(isUnhandledEscape({ key: 'Escape', defaultPrevented: false, target: target(true) })).toBe(false);
    expect(isUnhandledEscape({ key: 'Enter', defaultPrevented: false, target: target(false) })).toBe(false);
  });
});
