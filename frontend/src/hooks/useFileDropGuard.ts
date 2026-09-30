import { useEffect } from 'react';
import { carriesFiles } from '../lib/dragdrop';

/**
 * A file dropped outside the upload zone would make the browser open it and leave the app (aborting uploads and
 * answers in progress). Drop zones call preventDefault themselves; everywhere else the drop is refused.
 */
export function useFileDropGuard(): void {
  useEffect(() => {
    const onDragOver = (event: DragEvent) => {
      if (event.defaultPrevented || !carriesFiles(event.dataTransfer)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'none';
    };
    const onDrop = (event: DragEvent) => {
      if (carriesFiles(event.dataTransfer)) event.preventDefault();
    };
    window.addEventListener('dragover', onDragOver);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('dragover', onDragOver);
      window.removeEventListener('drop', onDrop);
    };
  }, []);
}
