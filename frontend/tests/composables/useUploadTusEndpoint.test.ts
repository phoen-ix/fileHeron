/* initUpload returns the server's tus endpoint (TUS_PUBLIC_BASE) and the SPA
 * ignored it, sending every resumable upload to the hardcoded '/uploads/'. */
import { defineComponent, h, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'

const fileStates: Array<[string, unknown]> = []
vi.mock('@uppy/tus', () => ({ default: class {} }))
vi.mock('@uppy/core', () => ({
  default: class FakeUppy {
    use() { return this }
    on() { return this }
    addFile() { return 'uppy-1' }
    setFileState(id: string, state: unknown) { fileStates.push([id, state]) }
    removeFile() {}
    getFile() { return null }
    getFiles() { return [] }
    cancelAll() {}
    upload() { return Promise.resolve() }
  },
}))
vi.mock('@/api/uploads', () => ({
  directUpload: vi.fn(),
  initUpload: vi.fn(async () => ({
    data: {
      file_id: 'f-1',
      tus_endpoint: '/files/tus/',
      upload_metadata_header: 'fh_payload x,fh_sig y',
    },
  })),
}))

import { useUpload } from '@/composables/useUpload'
import { useSiteStore } from '@/stores/site'

describe('useUpload resumable path', () => {
  it('sends the upload to the endpoint the server named', async () => {
    setActivePinia(createPinia())
    useSiteStore().maxDirectUploadBytes = 1 // everything goes resumable
    let u!: ReturnType<typeof useUpload>
    mount(defineComponent({
      setup() {
        u = useUpload(ref('share-1'))
        return () => h('div')
      },
    }))
    u.add([new File([new Uint8Array(10)], 'big.bin')])
    await u.start()
    await flushPromises()
    expect(fileStates).toContainEqual(['uppy-1', { tus: { endpoint: '/files/tus/' } }])
  })
})
