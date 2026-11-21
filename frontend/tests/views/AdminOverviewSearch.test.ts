/* Refining the setting search shrinks the result list; the highlight index was
 * never reset, so it pointed past the end - no row highlighted, and Enter did
 * nothing. Every new query starts at the top hit. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { afterEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

import { click, clickAway, press, type } from '../helpers/user'

vi.mock('@/api/admin', () => ({
  adminListFiles: vi.fn(() => new Promise(() => {})),
  getInboxUnreadCount: vi.fn(() => new Promise(() => {})),
  getSystemStatus: vi.fn(() => new Promise(() => {})),
  listIpBlocks: vi.fn(() => new Promise(() => {})),
  listSettingsChanges: vi.fn(() => new Promise(() => {})),
}))
vi.mock('@/api/shares', () => ({ listPendingApprovals: vi.fn(() => new Promise(() => {})) }))
const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }), RouterLink: { template: '<a><slot /></a>' } }))

import AdminOverview from '@/views/AdminOverview.vue'
import { useAuthStore } from '@/stores/auth'

const mounted: { unmount: () => void }[] = []
afterEach(() => {
  mounted.splice(0).forEach((w) => w.unmount())
  vi.useRealTimers()
  push.mockClear()
})

// In the document, so the field can really take the focus (tests/helpers/user.ts).
async function mountOverview() {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.user = { id: 1, role: 'admin', display_name: 'A' } as unknown as typeof auth.user
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(AdminOverview, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: { RouterLink: true, AdminPageHeader: { template: '<header><slot /></header>' } },
    },
  })
  mounted.push(w)
  await flushPromises()
  return w
}

const options = (w: Awaited<ReturnType<typeof mountOverview>>) => w.findAll('[role="option"]')
const listShown = (w: Awaited<ReturnType<typeof mountOverview>>) =>
  w.find('#ov-search-input').attributes('aria-expanded') === 'true'

describe('AdminOverview setting search', () => {
  it('a refined query highlights its first hit again', async () => {
    const w = await mountOverview()
    const input = w.find('#ov-search-input')
    await type(input, 'ma')
    const broad = options(w).length
    expect(broad).toBeGreaterThan(3)
    for (let i = 0; i < broad; i++) await press('ArrowDown')

    await type(input, 'mail log')
    expect(options(w).length).toBeGreaterThan(0)
    expect(options(w).length).toBeLessThan(broad)
    expect(options(w)[0].attributes('aria-selected')).toBe('true')
    await press('Enter')
    expect(push).toHaveBeenCalledTimes(1)
  })

  // The recipient picker's defect (2026-11-20): the list opened only when the
  // field GAINED the focus, and after Escape it still has it.
  it('opens the list again when the user types, or arrows, after Escape', async () => {
    const w = await mountOverview()
    const input = w.find('#ov-search-input')
    await type(input, 'mail')
    expect(listShown(w)).toBe(true)

    await press('Escape')
    expect(listShown(w)).toBe(false)
    await type(input, 'mail log')
    expect(listShown(w)).toBe(true)

    await press('Escape')
    await press('ArrowDown')
    expect(listShown(w)).toBe(true)
  })

  it('keeps the list open when the field is left and re-entered at once', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const w = await mountOverview()
    const input = w.find('#ov-search-input')
    await type(input, 'mail')

    await clickAway()
    vi.advanceTimersByTime(60)
    await click(input)
    vi.advanceTimersByTime(150)
    await flushPromises()
    expect(listShown(w)).toBe(true)

    await clickAway()
    vi.advanceTimersByTime(150)
    await flushPromises()
    expect(listShown(w)).toBe(false)
  })
})
