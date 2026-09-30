/** Whether a drag operation carries files from the operating system (not text or links dragged inside the page). */
export function carriesFiles(dataTransfer: Pick<DataTransfer, 'types'> | null): boolean {
  return Array.from(dataTransfer?.types ?? []).includes('Files');
}
