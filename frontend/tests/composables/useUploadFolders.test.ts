/* Folder uploads (v2.26.0): an upload from a picked or dropped folder sends
 * the folder on both paths, and a file on its own sends nothing new - so a
 * server that predates the field sees exactly what it always saw. */
import { defineComponent, h, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
      return 'uppy-1'
    }
    setFileState() {}
    removeFile() {}
    getFile() {
      return null
    }
    getFiles() {
      return []
    }
    cancelAll() {}
    upload() {
      return Promise.resolve()
    }
  },
}))
vi.mock('@/api/uploads', () => ({
  directUpload: vi.fn(async () => ({ data: { file_id: 'f-d', size_bytes: 1, sha256_hex: null } })),
  initUpload: vi.fn(async () => ({
    data: { file_id: 'f-r', tus_endpoint: '/uploads/', upload_metadata_header: 'h' },
  })),
}))

import { directUpload, initUpload } from '@/api/uploads'
import { useUpload } from '@/composables/useUpload'
import { useSiteStore } from '@/stores/site'

function picked(path: string): File {
  const f = new File([new Uint8Array(10)], path.split('/').pop() ?? 'x')
  Object.defineProperty(f, 'webkitRelativePath', { value: path })
  return f
}

async function upload(files: File[], directLimit: number) {
  setActivePinia(createPinia())
  useSiteStore().maxDirectUploadBytes = directLimit
  let u!: ReturnType<typeof useUpload>
  mount(
    defineComponent({
      setup() {
        u = useUpload(ref('share-1'))
        return () => h('div')
      },
    }),
  )
  u.add(files)
  await u.start()
  await flushPromises()
  return u
}

describe('useUpload with folders', () => {
  beforeEach(() => vi.clearAllMocks())

  it('the direct path sends the folder', async () => {
    const u = await upload([picked('Photos/2026/a.jpg')], 1024 * 1024)
    expect(u.items.value[0].relativeDir).toBe('Photos/2026')
    expect(vi.mocked(directUpload).mock.calls[0][3]).toBe('Photos/2026')
  })

  it('the resumable path sends the folder', async () => {
    await upload([picked('Photos/2026/big.bin')], 1)
    expect(vi.mocked(initUpload).mock.calls[0][0]).toMatchObject({ relative_dir: 'Photos/2026' })
  })

  it('a file on its own sends nothing new', async () => {
    await upload([new File([new Uint8Array(10)], 'plain.bin')], 1)
    expect(vi.mocked(initUpload).mock.calls[0][0]).not.toHaveProperty('relative_dir')
    await upload([new File([new Uint8Array(10)], 'plain.txt')], 1024 * 1024)
    expect(vi.mocked(directUpload).mock.calls[0][3]).toBeNull()
  })
})
