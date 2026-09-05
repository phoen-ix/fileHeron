/* One definition of "which uploads have landed" for the batch-complete call.
 * ShareCreate counted `done` only while its own all-done check and
 * ShareDetail's add-files panel counted `finalizing` too, so a share whose
 * files all went through tus reported the batch with an empty id list. */
import { describe, expect, it } from 'vitest'

import { settledFileIds, type UploadItem, type UploadState } from '@/composables/useUpload'

function item(state: UploadState, fileId: string | null): UploadItem {
  return {
    uid: `u-${state}-${fileId}`,
    file: new File(['x'], 'a.txt'),
    state,
    progress: 0,
    fileId,
    error: null,
    errorCode: null,
    bytesUploaded: 0,
  }
}

describe('settledFileIds', () => {
  it('counts done AND finalizing, never the rest, and never an item without an id', () => {
    const ids = settledFileIds([
      item('done', 'a'),
      item('finalizing', 'b'),
      item('uploading', 'c'),
      item('preparing', 'd'),
      item('queued', 'e'),
      item('error', 'f'),
      item('done', null),
    ])
    expect(ids).toEqual(['a', 'b'])
  })
})
