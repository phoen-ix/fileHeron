/* Tests the queue-management surface of useUpload. The actual upload
 * paths (direct + TUS) hit fetch / Uppy and are exercised in the
 * Phase 3a/3b backend smoke tests, not here. */
import { defineComponent, h, ref, type Ref } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

// Stub Uppy's plugin chain — the composable's add/remove/retry
// branches don't call into Uppy until start(), and we never call
// start() here.
vi.mock('@uppy/tus', () => ({ default: class {} }))
vi.mock('@uppy/core', () => {
  return {
    default: class FakeUppy {
      use() {
        return this
      }
      on() {
        return this
      }
      addFile() {
        return 'fake-id'
      }
      removeFile() {
        /* noop */
      }
      getFile() {
        return null
      }
      getFiles() {
        return []
      }
      cancelAll() {
        /* noop */
      }
      upload() {
        return Promise.resolve()
      }
    },
  }
})

import { useUpload } from '@/composables/useUpload'

function fakeFile(name: string, size: number): File {
  return new File([new Uint8Array(size)], name, { type: 'text/plain' })
}

// useUpload calls onBeforeUnmount internally to cancel in-flight
// Uppy uploads on teardown. Vue requires lifecycle hooks be
// registered during a component's setup() — calling the composable
// bare emits a "no active component instance" warning. Mounting it
// inside a tiny host binds the hook to a real lifecycle.
function makeHost(shareId: Ref<string | null>) {
  return defineComponent({
    setup() {
      const u = useUpload(shareId)
      return { u }
    },
    render() {
      return h('div')
    },
  })
}

describe('useUpload', () => {
  it('add() enqueues files in queued state', () => {
    const shareId = ref<string | null>('share-uuid')
    const wrapper = mount(makeHost(shareId))
    const u = wrapper.vm.u
    u.add([fakeFile('a.txt', 10), fakeFile('b.txt', 20)])
    expect(u.items.value).toHaveLength(2)
    expect(u.items.value[0].state).toBe('queued')
    expect(u.items.value[0].progress).toBe(0)
    expect(u.items.value[1].file.name).toBe('b.txt')
    wrapper.unmount()
  })

  it('remove() removes by uid', () => {
    const shareId = ref<string | null>('share-uuid')
    const wrapper = mount(makeHost(shareId))
    const u = wrapper.vm.u
    u.add([fakeFile('a.txt', 10), fakeFile('b.txt', 20)])
    const targetUid = u.items.value[0].uid
    u.remove(targetUid)
    expect(u.items.value).toHaveLength(1)
    expect(u.items.value[0].file.name).toBe('b.txt')
    wrapper.unmount()
  })

  it('retry() resets state to queued', async () => {
    const shareId = ref<string | null>('share-uuid')
    const wrapper = mount(makeHost(shareId))
    const u = wrapper.vm.u
    u.add([fakeFile('a.txt', 10)])
    const item = u.items.value[0]
    item.state = 'error'
    item.error = 'simulated'
    item.progress = 42
    // Make startItem fail without hitting the network.
    const promise = u.retry(item.uid)
    // After retry resets, state should be queued/preparing/error depending
    // on async timing — at minimum the previous error is cleared synchronously.
    expect(item.error).toBeNull()
    expect(item.progress).toBe(0)
    await promise
    wrapper.unmount()
  })

  it('totalBytes / uploadedBytes reflect the queue', () => {
    const shareId = ref<string | null>('share-uuid')
    const wrapper = mount(makeHost(shareId))
    const u = wrapper.vm.u
    u.add([fakeFile('a.txt', 100), fakeFile('b.txt', 250)])
    expect(u.totalBytes.value).toBe(350)
    expect(u.uploadedBytes.value).toBe(0)
    u.items.value[0].bytesUploaded = 50
    expect(u.uploadedBytes.value).toBe(50)
    wrapper.unmount()
  })

  it('isActive is false for an empty queue', () => {
    const shareId = ref<string | null>('share-uuid')
    const wrapper = mount(makeHost(shareId))
    const u = wrapper.vm.u
    expect(u.isActive.value).toBe(false)
    wrapper.unmount()
  })
})
