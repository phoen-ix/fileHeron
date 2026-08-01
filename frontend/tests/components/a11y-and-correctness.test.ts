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

    // useSSE registers onBeforeUnmount, so calling it bare warns "called when
    // there is no active component instance" and - more to the point - leaves
    // the composable's teardown unregistered, which is not how it runs in the
    // app. Mount it the way a component does.
    let sse!: ReturnType<typeof useSSE>
    const host = defineComponent({
      setup() {
        sse = useSSE({ url: '/api/stream', onMessage() {} })
        return () => null
      },
    })
    const wrapper = mount(host)

    sse.start()
    sse.start()
    sse.start()
    await nextTick()
    await new Promise((r) => setTimeout(r, 0))

    expect(FakeES.instances).toBe(1)
    wrapper.unmount()
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

  it('every route HAS a title key, and it resolves in both locales', () => {
    const src = source('/src/router/index.ts')
    const keys = [...src.matchAll(/titleKey: '([^']+)'/g)].map((m) => m[1])
    // Counting the keys that exist cannot detect a route that LOST one -
    // proven by mutation: removing `titleKey: 'login'` left all 17 tests in
    // this file green while the login page reverted to the bare 'file:Heron'
    // title, which is the exact regression the note in router/index.ts cites
    // (audit #2). Compare against the route count instead.
    // Routes that RENDER something. A pure `redirect:` entry and the /admin
    // layout wrapper show no page of their own, so they carry no title.
    const rendering = src
      .split(/\n(?=\s*\{)/)
      .filter((block) => /^\s*path: '/m.test(block) && /component:/.test(block))
      .filter((block) => !/redirect:/.test(block))
      .filter((block) => !/AdminLayout\.vue/.test(block))
    expect(rendering.length).toBeGreaterThan(20)
    const withoutKey = rendering
      .filter((block) => !/titleKey:/.test(block))
      .map((block) => /path: '([^']+)'/.exec(block)?.[1])
    expect(withoutKey, 'route(s) missing a titleKey').toEqual([])
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

  it('every view passes the count as the plural argument', () => {
    // `t(key, { n })` alone always renders the plural branch - the count has to
    // be the last argument. Checking ONE component could not see PublicShare
    // dropping it, which made a link with one download left read "1 downloads
    // left" (audit #2). Scan every source instead.
    // Only keys that ACTUALLY have a plural form (a `|` in the message) need
    // the count - `Files ({n})` is one message and passing a count would be
    // noise.
    const messages = en as Messages
    const isPlural = (key: string): boolean => {
      const value = key
        .split('.')
        .reduce<unknown>((acc, part) => (acc as Messages)?.[part], messages)
      return typeof value === 'string' && value.includes('|')
    }
    const offenders: string[] = []
    for (const [file, src] of Object.entries(SOURCES)) {
      for (const m of src.matchAll(/\bt\(\s*'([^']+)'\s*,\s*\{\s*n:\s*([^}]+)\}\s*([,)])/g)) {
        if (m[3] === ')' && isPlural(m[1])) offenders.push(`${file}: ${m[1]}`)
      }
    }
    expect(offenders, 'plural call with no count argument').toEqual([])
  })
})

// fe-i18n-a11y-2 (bell headlines vs the backend's dispatched categories) is
// asserted in backend/tests/test_gate_wiring_coverage.py: the frontend test
// container only mounts frontend/, so the enum it must be compared against is
// not reachable from here.
