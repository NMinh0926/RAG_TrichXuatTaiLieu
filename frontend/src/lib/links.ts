/**
 * Links in LLM answers are not clickable: a shared document could prompt-inject a link that leaks context or
 * phishes. They are shown as text followed by their address so the user can judge and copy it deliberately.
 */

/** The address to show after a link's text, or null when the text already is the address (bare URLs). */
export function linkAddressNote(href: string | undefined, text: string): string | null {
  if (!href) return null;
  const shown = text.trim();
  const bare = href.replace(/^(https?:\/\/|mailto:)/i, '');
  return shown === href || shown === bare ? null : href;
}
