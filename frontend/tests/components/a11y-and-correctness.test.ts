/* The frontend half of the 2026-07-30 audit.
 *
 * Grouped here because they share one shape: a control that looks like it
 * works. A row you can see but not reach, an Escape key bound where it can
 * never fire, a dropdown that keeps a value the server rejected, a download
 * link for a file the backend always refuses.
 *
 * No axe: happy-dom has no layout engine, so its contrast/visibility/focus
 * rules silently no-op. Attribute and behaviour assertions only; the e2e suite
 * carries what needs a real browser. */

import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { defineComponent, h, nextTick, ref } from 'vue'

import de from '@/i18n/locales/de.json'
import en from '@/i18n/locales/en.json'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en, de } })

/* Source text of the files these assertions are about. `import.meta.glob` is
 * typed by vite/client and resolves under vue-tsc, unlike a bare `?raw`
 * specifier - which type-checks as a missing module even though vite serves
 * it fine. */
const SOURCES = import.meta.glob('/src/**/*.{ts,vue}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

function source(path: string): string {
  const found = SOURCES[path]
  if (found === undefined) throw new Error(`no source for ${path}`)
  return found
}

type Messages = Record<string, Record<string, unknown>>

beforeEach(() => {
  setActivePinia(createPinia())
})

// --- fe-i18n-a11y-7: Escape actually closes ---------------------------------

describe('useEscapeToClose', () => {
  it('closes on Escape from anywhere in the document', async () => {
    const { useEscapeToClose } = await import('@/composables/useEscapeToClose')
    const closed = vi.fn()
    const open = ref(true)

    const Host = defineComponent({
      setup() {
        useEscapeToClose(open, closed)
        return () => h('div')
      },
    })
    mount(Host, { attachTo: document.body })

    // The key event target is the BODY, never the modal backdrop - which is
    // exactly why the `@keydown.escape` the backdrops carried never fired.
    document.body.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    )
    expect(closed).toHaveBeenCalledTimes(1)
  })

  it('does nothing while the modal is closed', async () => {
    const { useEscapeToClose } = await import('@/composables/useEscapeToClose')
    const closed = vi.fn()
    const open = ref(false)
    const Host = defineComponent({
      setup() {
        useEscapeToClose(open, closed)
        return () => h('div')
      },
    })
    mount(Host, { attachTo: document.body })

    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(closed).not.toHaveBeenCalled()
  })

  it('ignores other keys', async () => {
    const { useEscapeToClose } = await import('@/composables/useEscapeToClose')
    const closed = vi.fn()
    const Host = defineComponent({
      setup() {
        useEscapeToClose(ref(true), closed)
        return () => h('div')
      },
    })
    mount(Host, { attachTo: document.body })
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    expect(closed).not.toHaveBeenCalled()
  })

  it('unbinds when the modal closes, so a stale handler cannot fire', async () => {
    const { useEscapeToClose } = await import('@/composables/useEscapeToClose')
    const closed = vi.fn()
    const open = ref(true)
    const Host = defineComponent({
      setup() {
        useEscapeToClose(open, closed)
        return () => h('div')
      },
    })
    mount(Host, { attachTo: document.body })
    open.value = false
    await nextTick()
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(closed).not.toHaveBeenCalled()
  })

  it('unbinds on unmount', async () => {
    const { useEscapeToClose } = await import('@/composables/useEscapeToClose')
    const closed = vi.fn()
    const Host = defineComponent({
      setup() {
        useEscapeToClose(ref(true), closed)
        return () => h('div')
      },
    })
    const wrapper = mount(Host, { attachTo: document.body })
    wrapper.unmount()
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(closed).not.toHaveBeenCalled()
  })
})

// --- fe-i18n-a11y-13: one formatBytes, locale-aware -------------------------

describe('formatBytes', () => {
  it('uses the active locale decimal separator', async () => {
    const { formatBytes } = await import('@/utils/bytes')
    const { i18n: appI18n } = await import('@/i18n')

    appI18n.global.locale.value = 'en'
    expect(formatBytes(1536)).toBe('1.5 KB')
    appI18n.global.locale.value = 'de'
    expect(formatBytes(1536)).toBe('1,5 KB')
    appI18n.global.locale.value = 'en'
  })

  it('keeps whole numbers for bytes and for large values', async () => {
    const { formatBytes } = await import('@/utils/bytes')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(null)).toBe('0 B')
    expect(formatBytes(300 * 1024 * 1024)).toBe('300 MB')
  })

  it('is the only definition left', () => {
    // Two components carried their own copy with a different precision rule, so
    // the same file read "1.46 MB" in one list and "1.5 MB" in another.
    const offenders = Object.entries(SOURCES)
      .filter(([path]) => path.endsWith('.vue'))
      .filter(([, src]) => /function formatBytes\s*\(/.test(src))
      .map(([path]) => path)
    expect(offenders).toEqual([])
  })
})

// --- fe-correct-1: one stream per mount -------------------------------------

describe('useSSE.start', () => {
  it('is idempotent while a connection is live', async () => {
    class FakeES {
      static instances = 0
      onopen: (() => void) | null = null
      onerror: (() => void) | null = null
      constructor() {
        FakeES.instances += 1
      }
      addEventListener() {}
      close() {}
    }
    vi.stubGlobal('EventSource', FakeES as unknown as typeof EventSource)

    const { useSSE } = await import('@/composables/useSSE')
    const sse = useSSE({ url: '/api/stream', onMessage() {} })

    sse.start()
    sse.start()
    sse.start()
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))

    expect(FakeES.instances).toBe(1)
    sse.stop()
    vi.unstubAllGlobals()
  })
})

// --- fe-i18n-a11y-3 / -10: the localized shell ------------------------------

describe('localization of the app shell', () => {
  it('every route carries a title KEY, not an English string', () => {
    const src = source('/src/router/index.ts')
    expect(src).not.toMatch(/title: '/)
    expect(src).toMatch(/titleKey: '/)
  })

  it('every title key resolves in both locales', () => {
    const src = source('/src/router/index.ts')
    const keys = [...src.matchAll(/titleKey: '([^']+)'/g)].map((m) => m[1])
    expect(keys.length).toBeGreaterThan(20)
    for (const key of keys) {
      expect((en as Messages).page_title[key], `en page_title.${key}`).toBeTruthy()
      expect((de as Messages).page_title[key], `de page_title.${key}`).toBeTruthy()
    }
  })

  it('sets <html lang> for the initial locale, not only on a switch', () => {
    const src = source('/src/i18n/index.ts')
    expect(src).toMatch(/document\.documentElement\.lang = initialLocale/)
  })
})

// --- fe-i18n-a11y-12: plural forms ------------------------------------------

describe('pluralisation', () => {
  it.each([
    ['expiry.in_days', 1, 'in 1 day'],
    ['expiry.in_days', 7, 'in 7 days'],
    ['expiry.in_hours', 1, 'in 1 hour'],
  ])('%s with n=%i renders %s', (key, n, expected) => {
    expect(i18n.global.t(key, { n }, n)).toBe(expected)
  })

  it('German picks the singular too', () => {
    const deI18n = createI18n({ legacy: false, locale: 'de', messages: { en, de } })
    expect(deI18n.global.t('expiry.in_days', { n: 1 }, 1)).toBe('in 1 Tag')
    expect(deI18n.global.t('expiry.in_days', { n: 3 }, 3)).toBe('in 3 Tagen')
  })

  it('the views pass the count as the plural argument', () => {
    // `t(key, { n })` alone always renders the plural branch - the count has to
    // be the second (or third) argument.
    const src = source('/src/components/ExpiryPicker.vue')
    expect(src).toMatch(/t\('expiry\.in_days', \{ n: days \}, days\)/)
  })
})

// fe-i18n-a11y-2 (bell headlines vs the backend's dispatched categories) is
// asserted in backend/tests/test_gate_wiring_coverage.py: the frontend test
// container only mounts frontend/, so the enum it must be compared against is
// not reachable from here.
