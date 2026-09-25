/* A failed 2FA status fetch left the status null, which the page rendered as
 * "Two-factor auth is off" plus a setup prompt - telling someone who HAS 2FA
 * that they do not. Unknown is shown as unknown, with a retry. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import en from '@/i18n/locales/en.json'

const getStatus = vi.fn()
vi.mock('@/api/twoFactor', () => ({ getStatus: () => getStatus() }))
vi.mock('@/api/account', () => ({ listSessions: vi.fn(async () => ({ data: { items: [] } })) }))
vi.mock('@/composables/useScrollSpy', () => ({
  useScrollSpy: () => ({ active: ref(null), lockTo: vi.fn() }),
}))

import Account from '@/views/Account.vue'
import { useAuthStore } from '@/stores/auth'

function makeWrapper() {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.user = {
    id: 1, display_name: 'Ada', role: 'employee', locale: 'en', default_landing_page: null,
    admin_nav_collapse_mode: null,
  } as unknown as typeof auth.user
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(Account, {
    global: {
      plugins: [i18n],
      stubs: {
        RouterLink: true, SectionQuickNav: true, ApiTokenPanel: true, NotificationPreferences: true,
        OIDCConnectPanel: true, PasswordStrength: true, SessionRow: true, WebAuthnPanel: true,
      },
    },
  })
}

describe('Account 2FA section', () => {
  beforeEach(() => getStatus.mockReset())

  it('an unknown status is not reported as "off"', async () => {
    getStatus.mockRejectedValueOnce(new Error('network'))
    getStatus.mockResolvedValueOnce({ data: { enabled: true, recovery_codes_remaining: 8 } })
    const w = makeWrapper()
    await flushPromises()
    const section = w.find('[id="2fa"]')
    expect(section.text()).not.toContain(en.account.twofa_off)
    expect(section.find('[role="alert"]').text()).toContain(en.account.twofa_status_unavailable)

    await section.find('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(w.find('[id="2fa"]').text()).toContain(en.account.twofa_on)
  })
})
