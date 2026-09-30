/** Question scope: the documents a chat question is restricted to (sent as `document_ids`). */
import { ApiError } from '../api/client';
import type { DocumentInfo } from '../api/types';

export interface ScopedDocument {
  id: string;
  filename: string;
}

/** Matches the backend limit on `document_ids` (ChatRequest). */
export const MAX_SCOPE_DOCUMENTS = 50;

export function scopeFull(selected: readonly ScopedDocument[]): boolean {
  return selected.length >= MAX_SCOPE_DOCUMENTS;
}

/** Adds or removes a document; adding to a full scope leaves it unchanged. */
export function toggleScoped(selected: ScopedDocument[], document: ScopedDocument): ScopedDocument[] {
  if (selected.some((item) => item.id === document.id)) return selected.filter((item) => item.id !== document.id);
  if (scopeFull(selected)) return selected;
  return [...selected, { id: document.id, filename: document.filename }];
}

/** Scoped documents the user can no longer see (deleted or unshared elsewhere): the API answers 404 for them. */
export async function findMissingDocuments(
  documents: readonly ScopedDocument[],
  fetchDocument: (id: string) => Promise<DocumentInfo>,
): Promise<ScopedDocument[]> {
  const missing = await Promise.all(
    documents.map((document) =>
      fetchDocument(document.id).then(
        () => false,
        (caught: unknown) => caught instanceof ApiError && caught.status === 404,
      ),
    ),
  );
  return documents.filter((_, i) => missing[i]);
}

export function missingScopeMessage(filenames: readonly string[]): string {
  return `Tài liệu sau không còn tồn tại hoặc không còn được chia sẻ nên đã được bỏ khỏi phạm vi hỏi: ${filenames.join(', ')}. Hãy gửi lại câu hỏi.`;
}
