import { describe, expect, it } from 'vitest';
import { hasUnsentUploads, type UploadItem, type UploadPhase } from './useUploads';

function item(phase: UploadPhase): UploadItem {
  return { key: phase, filename: `${phase}.pdf`, phase, job: null, error: null, warnings: [] };
}

describe('hasUnsentUploads', () => {
  it('is true only while a file is still being sent', () => {
    expect(hasUnsentUploads([item('succeeded'), item('uploading')])).toBe(true);
    expect(hasUnsentUploads([item('processing'), item('failed'), item('duplicate')])).toBe(false);
    expect(hasUnsentUploads([])).toBe(false);
  });
});
