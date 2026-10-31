/* The direct-vs-TUS branch in useUpload.startItem(): under the ceiling goes
 * through a single directUpload(); at/over it goes the resumable path
 * (initUpload + Uppy.addFile + Uppy.upload). Sizes are faked via a redefined
 * `size` so we never allocate 100 MB. Mirrors the Uppy-mock style of
 * useUpload.test.ts.
 *
 * The ceiling is the LIVE server value from the site store, not a build-time
 * constant - an admin lowering `uploads.max_direct_bytes` used to make every
 * file between the new cap and 100 MB stream in full and then fail (audit #2).
 * The store's default matches the old constant, so these cases are unchanged. */

import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h, ref, type Ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => {
  setActivePinia(createPinia())
})

const uppyCalls = { addFile: 0, upload: 0, meta: [] as unknown[] }
const uppyOn: Record<string, (...args: unknown[]) => void> = {}
vi.mock('@uppy/tus', () => ({ default: class {} }))
vi.mock('@uppy/core', () => ({
  default: class FakeUppy {
    use() {
      return this
    }
    on(event: string, fn: (...args: unknown[]) => void) {
      uppyOn[event] = fn
      return this
    }
    addFile(f: { meta: unknown }) {
      uppyCalls.addFile++
      uppyCalls.meta.push(f.meta)
      return 'fake-id'
    }
    removeFile() {}
    getFile() {
      return { meta: uppyCalls.meta.at(-1) }
    }
    getFiles() {
      return []
    }
    cancelAll() {}
    upload() {
      uppyCalls.upload++
      return Promise.resolve()
    }
  },
}))

const directUpload = vi.fn()
const initUpload = vi.fn()
vi.mock('@/api/uploads', () => ({
  directUpload: (...args: unknown[]) => directUpload(...args),
  initUpload: (...args: unknown[]) => initUpload(...args),
}))

import { useUpload } from '@/composables/useUpload'

const MB = 1024 * 1024

function fileOfSize(name: string, size: number): File {
  const f = new File([new Uint8Array(0)], name, { type: 'application/octet-stream' })
  Object.defineProperty(f, 'size', { value: size })
  return f
}

function makeHost(shareId: Ref<string | null>) {
  return defineComponent({
    setup() {
      return { u: useUpload(shareId) }
    },
    render() {
      return h('div')
    },
  })
}

beforeEach(() => {
  uppyCalls.addFile = 0
  uppyCalls.upload = 0
  uppyCalls.meta = []
  directUpload.mockReset().mockResolvedValue({ data: { file_id: 'fid-direct' } })
  initUpload
    .mockReset()
    .mockResolvedValue({ data: { file_id: 'fid-tus', upload_metadata_header: 'meta-hdr' } })
})

describe('useUpload direct-vs-TUS threshold', () => {
  it('a file just under 100 MB uploads directly (no init, no Uppy)', async () => {
    const wrapper = mount(makeHost(ref('share-1')))
    const u = wrapper.vm.u
    u.add([fileOfSize('small.bin', 100 * MB - 1)])
    await u.start()

    expect(directUpload).toHaveBeenCalledTimes(1)
    expect(initUpload).not.toHaveBeenCalled()
    expect(uppyCalls.addFile).toBe(0)
    expect(u.items.value[0].state).toBe('done')
    expect(u.items.value[0].fileId).toBe('fid-direct')
    wrapper.unmount()
  })

  it('a file over 100 MB takes the resumable TUS path (init + Uppy)', async () => {
    const wrapper = mount(makeHost(ref('share-1')))
    const u = wrapper.vm.u
    u.add([fileOfSize('big.bin', 100 * MB + 1)])
    await u.start()

    expect(initUpload).toHaveBeenCalledTimes(1)
    expect(initUpload).toHaveBeenCalledWith(
      expect.objectContaining({
        share_id: 'share-1',
        filename: 'big.bin',
        size_bytes: 100 * MB + 1,
      }),
    )
    expect(directUpload).not.toHaveBeenCalled()
    expect(uppyCalls.addFile).toBe(1)
    expect(uppyCalls.upload).toBe(1)
    expect(u.items.value[0].fileId).toBe('fid-tus')
    wrapper.unmount()
  })

  it('a Retry carries on with the same file - no second init', async () => {
    // tus resumes the failed upload by its fingerprint, so its bytes complete
    // the FIRST file; a second init left a row that never got any.
    const wrapper = mount(makeHost(ref('share-1')))
    const u = wrapper.vm.u
    u.add([fileOfSize('big.bin', 100 * MB + 1)])
    await u.start()
    const item = u.items.value[0]
    item.state = 'error' // what Uppy's upload-error handler leaves behind
    await u.retry(item.uid)

    expect(initUpload).toHaveBeenCalledTimes(1)
    expect(uppyCalls.addFile).toBe(2)
    expect(uppyCalls.meta[1]).toEqual(uppyCalls.meta[0])
    expect(item.fileId).toBe('fid-tus')
    wrapper.unmount()
  })

  it('a Retry after the server refused the envelope registers the file afresh', async () => {
    // pre-create refuses an expired envelope or a row the stale sweep ended;
    // no upload exists to resume, so only a new init can carry on.
    const wrapper = mount(makeHost(ref('share-1')))
    const u = wrapper.vm.u
    u.add([fileOfSize('big.bin', 100 * MB + 1)])
    await u.start()
    const item = u.items.value[0]
    uppyOn['upload-error']!(
      { id: 'fake-id' },
      new Error(
        'tus: unexpected response while creating upload, originated from request (method: POST, ' +
          'response code: 500, response text: ERR_INTERNAL_SERVER_ERROR: unexpected response code ' +
          'from hook endpoint (403): {"error":"Upload authorisation expired.","code":"TUS_ENVELOPE_EXPIRED"})',
      ),
    )
    expect(item.state).toBe('error')
    initUpload.mockResolvedValueOnce({
      data: { file_id: 'fid-tus-2', upload_metadata_header: 'meta-hdr-2' },
    })
    await u.retry(item.uid)

    expect(initUpload).toHaveBeenCalledTimes(2)
    expect(uppyCalls.meta[1]).toMatchObject({
      fileId: 'fid-tus-2',
      uploadMetadataHeader: 'meta-hdr-2',
    })
    expect(item.fileId).toBe('fid-tus-2')
    wrapper.unmount()
  })

  it('errors the item (no upload) when there is no share id', async () => {
    const wrapper = mount(makeHost(ref<string | null>(null)))
    const u = wrapper.vm.u
    u.add([fileOfSize('x.bin', 10)])
    await u.start()
    expect(directUpload).not.toHaveBeenCalled()
    expect(initUpload).not.toHaveBeenCalled()
    expect(u.items.value[0].state).toBe('error')
    wrapper.unmount()
  })
})
