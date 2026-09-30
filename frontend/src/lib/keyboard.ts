type KeyEventLike = Pick<KeyboardEvent, 'key' | 'defaultPrevented' | 'target'>;

/**
 * Whether an Escape press is free for a page-level action (closing the document viewer): menus and popovers
 * that close on Escape mark it handled with preventDefault(), and Escape inside a dialog belongs to that dialog.
 */
export function isUnhandledEscape(event: KeyEventLike): boolean {
  if (event.key !== 'Escape' || event.defaultPrevented) return false;
  const target = event.target as Partial<Element> | null;
  return !(typeof target?.closest === 'function' && target.closest('dialog, [role="dialog"], [role="menu"]'));
}
