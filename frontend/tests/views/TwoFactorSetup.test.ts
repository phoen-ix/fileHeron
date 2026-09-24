/* Two error paths on the 2FA page rendered nothing: regenerating recovery
 * codes with a wrong password/code (the active stage had no error element), and
 * a failed status fetch (the page said "Loading..." forever). */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}))
const getStatus = vi.fn()
const regenerateRecoveryCodes = vi.fn()
vi.mock('@/api/twoFactor', () => ({
  getStatus: () => getStatus(),
  regenerateRecoveryCodes: (p: unknown) => regenerateRecoveryCodes(p),
  beginSetup: vi.fn(),
  enable: vi.fn(),
  disable: vi.fn(),
}))

import TwoFactorSetup from '@/views/TwoFactorSetup.vue'

const envelope = (code: string, status: number) =>
  Object.assign(new Error(code), { isAxiosError: true, response: { status, data: { code, error: code } } })

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(TwoFactorSetup, { global: { plugins: [i18n], stubs: { RouterLink: true } } })
}

describe('TwoFactorSetup error states', () => {
  beforeEach(() => {
    getStatus.mockReset()
    regenerateRecoveryCodes.mockReset()
  })

  it('a failed status fetch offers a retry instead of loading forever', async () => {
    getStatus.mockRejectedValueOnce(envelope('SERVER_ERROR', 500))
    getStatus.mockResolvedValueOnce({ data: { enabled: true, recovery_codes_remaining: 8 } })
    const w = makeWrapper()
    await flushPromises()
    const alert = w.find('[role="alert"]')
    expect(alert.exists()).toBe(true)
    await alert.find('button').trigger('click')
    await flushPromises()
    expect(w.text()).toContain(en.twofa.active_title)
  })

  it('a refused recovery-code regeneration says why', async () => {
    getStatus.mockResolvedValueOnce({ data: { enabled: true, recovery_codes_remaining: 8 } })
    regenerateRecoveryCodes.mockRejectedValueOnce(envelope('INVALID_TOTP', 401))
    const w = makeWrapper()
    await flushPromises()
    await w.find('#regen-password').setValue('pw')
    await w.find('#regen-code').setValue('123456')
    await w.find('form.manage-form').trigger('submit')
    await flushPromises()
    expect(w.find('[role="alert"]').text()).toBe(en.errors.INVALID_TOTP)
  })
})
