/* The 100 MB direct-vs-TUS branch in useUpload.startItem(): under the threshold
 * goes through a single directUpload(); at/over it goes the resumable path
 * (initUpload + Uppy.addFile + Uppy.upload). Sizes are faked via a redefined
 * `size` so we never allocate 100 MB. Mirrors the Uppy-mock style of
 * useUpload.test.ts. */

import { mount } from '@vue/test-utils'
import { defineComponent, h, ref, type Ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const uppyCalls = { addFile: 0, upload: 0 }
vi.mock('@uppy/tus', () => ({ default: class {} }))
vi.mock('@uppy/core', () => ({
  default: class FakeUppy {
    use() {
      return this
    }
    on() {
      return this
    }
    addFile() {
      uppyCalls.addFile++
      return 'fake-id'
    }
    removeFile() {}
    getFile() {
      return null
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
      expect.objectContaining({ share_id: 'share-1', filename: 'big.bin', size_bytes: 100 * MB + 1 }),
    )
    expect(directUpload).not.toHaveBeenCalled()
    expect(uppyCalls.addFile).toBe(1)
    expect(uppyCalls.upload).toBe(1)
    expect(u.items.value[0].fileId).toBe('fid-tus')
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
