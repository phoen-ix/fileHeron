/* Refining the setting search shrinks the result list; the highlight index was
 * never reset, so it pointed past the end - no row highlighted, and Enter did
 * nothing. Every new query starts at the top hit. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

vi.mock('@/api/admin', () => ({
  adminListFiles: vi.fn(() => new Promise(() => {})),
  getInboxUnreadCount: vi.fn(() => new Promise(() => {})),
  getSystemStatus: vi.fn(() => new Promise(() => {})),
  listIpBlocks: vi.fn(() => new Promise(() => {})),
}))
vi.mock('@/api/shares', () => ({ listPendingApprovals: vi.fn(() => new Promise(() => {})) }))
const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }), RouterLink: { template: '<a><slot /></a>' } }))

import AdminOverview from '@/views/AdminOverview.vue'
import { useAuthStore } from '@/stores/auth'

describe('AdminOverview setting search', () => {
  it('a refined query highlights its first hit again', async () => {
    setActivePinia(createPinia())
    const auth = useAuthStore()
    auth.user = { id: 1, role: 'admin', display_name: 'A' } as unknown as typeof auth.user
    const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
    const w = mount(AdminOverview, {
      global: {
        plugins: [i18n],
        stubs: { RouterLink: true, AdminPageHeader: { template: '<header><slot /></header>' } },
      },
    })
    await flushPromises()
    const input = w.find('#ov-search-input')
    await input.trigger('focus')
    await input.setValue('ma')
    const broad = w.findAll('[role="option"]').length
    expect(broad).toBeGreaterThan(3)
    for (let i = 0; i < broad; i++) await input.trigger('keydown', { key: 'ArrowDown' })

    await input.setValue('mail log')
    const options = w.findAll('[role="option"]')
    expect(options.length).toBeGreaterThan(0)
    expect(options.length).toBeLessThan(broad)
    expect(options[0].attributes('aria-selected')).toBe('true')
    await input.trigger('keydown', { key: 'Enter' })
    expect(push).toHaveBeenCalledTimes(1)
  })
})
