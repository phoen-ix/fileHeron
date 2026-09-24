/* Leaving ShareCreate/ShareDetail unmounts useUpload, which cancels the queue -
 * and for a resumable upload @uppy/tus turns cancel into a server-side DELETE.
 * So navigating away mid-transfer threw the bytes away without asking. */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h, ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

let leaveGuard: (() => boolean | Promise<boolean>) | null = null
vi.mock('vue-router', () => ({
  onBeforeRouteLeave: (fn: () => boolean | Promise<boolean>) => {
    leaveGuard = fn
  },
}))

import { useUploadLeaveGuard } from '@/composables/useUploadLeaveGuard'
import { useUiStore } from '@/stores/ui'

const mounted: Array<{ unmount: () => void }> = []

function mountWith(active: boolean) {
  setActivePinia(createPinia())
  const flag = ref(active)
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const Host = defineComponent({
    setup() {
      useUploadLeaveGuard(flag)
      return () => h('div')
    },
  })
  const w = mount(Host, { global: { plugins: [i18n] } })
  mounted.push(w)
  return { w, flag }
}

describe('useUploadLeaveGuard', () => {
  beforeEach(() => {
    leaveGuard = null
  })
  // Each mount registers a window listener; one left behind by an earlier test
  // would answer for this one.
  afterEach(() => {
    for (const w of mounted.splice(0)) {
      try {
        w.unmount()
      } catch {
        /* already unmounted by the test */
      }
    }
  })

  it('asks before an in-app navigation while uploads run', async () => {
    mountWith(true)
    const ui = useUiStore()
    const confirm = vi.spyOn(ui, 'confirm').mockResolvedValue(false)
    expect(await leaveGuard!()).toBe(false)
    expect(confirm).toHaveBeenCalledOnce()
    expect(confirm.mock.calls[0][0].message).toBe(en.upload.leave.message)
  })

  it('lets navigation through when nothing is uploading', async () => {
    mountWith(false)
    const confirm = vi.spyOn(useUiStore(), 'confirm')
    expect(await leaveGuard!()).toBe(true)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('holds a reload or tab close only while uploads run', () => {
    const { w, flag } = mountWith(true)
    const busy = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(busy)
    expect(busy.defaultPrevented).toBe(true)

    flag.value = false
    const idle = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(idle)
    expect(idle.defaultPrevented).toBe(false)

    w.unmount()
    flag.value = true
    const gone = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(gone)
    expect(gone.defaultPrevented).toBe(false)
  })
})
