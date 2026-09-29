import { useSyncExternalStore } from 'react';

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const list = matchMedia(query);
      list.addEventListener('change', onChange);
      return () => list.removeEventListener('change', onChange);
    },
    () => matchMedia(query).matches,
  );
}

/** Tailwind `lg` breakpoint: sidebar and viewer are docked columns from here up, drawers below. */
export const DESKTOP_QUERY = '(min-width: 1024px)';
