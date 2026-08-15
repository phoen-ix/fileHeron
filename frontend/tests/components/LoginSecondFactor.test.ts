/* The interstitial is the only place the second factor is demanded for an SSO
 * or passkey login, so it needs cover in its own right - the backend can be
 * perfect and this view can still strand the user. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const completeSecondFactor = vi.fn(async () => ({}))
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ completeSecondFactor }),
}))

const replace = vi.fn(async () => {})
let query: Record<string, string> = { pending: 'pending-token-abc' }
vi.mock('vue-router', () => ({
  useRoute: () => ({ query }),
  useRouter: () => ({ replace }),
}))

import LoginSecondFactor from '@/views/LoginSecondFactor.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(LoginSecondFactor, { global: { plugins: [i18n] } })
}

describe('LoginSecondFactor', () => {
  beforeEach(() => {
    completeSecondFactor.mockClear()
    replace.mockClear()
    query = { pending: 'pending-token-abc' }
    completeSecondFactor.mockImplementation(async () => ({}))
  })

  it('sends the pending token with the TOTP code', async () => {
    const w = makeWrapper()
    await flushPromises()
    await w.find('input').setValue('123 456')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(completeSecondFactor).toHaveBeenCalledWith('pending-token-abc', {
      totpCode: '123456', // whitespace stripped, as the password flow does
    })
  })

  it('can switch to a recovery code', async () => {
    const w = makeWrapper()
    await flushPromises()
    const toggle = w.findAll('button').filter((b) => b.text().includes('recovery code'))[0]
    await toggle.trigger('click')
    await w.find('input').setValue('abcd-efgh')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(completeSecondFactor).toHaveBeenCalledWith('pending-token-abc', {
      recoveryCode: 'abcd-efgh',
    })
  })

  it('strips the token from the address bar on mount', async () => {
    makeWrapper()
    await flushPromises()
    // Second arg-less replace to the same route: the token must not linger in
    // history where it can be copied or restored.
    expect(replace).toHaveBeenCalledWith({ name: 'login-2fa' })
  })

  it('sends the user back to login when there is no pending token', async () => {
    query = {}
    makeWrapper()
    await flushPromises()
    expect(replace).toHaveBeenCalledWith({ name: 'login' })
    expect(completeSecondFactor).not.toHaveBeenCalled()
  })

  it('sends the user back to login when the pending token has expired', async () => {
    completeSecondFactor.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 401, data: { code: 'PENDING_2FA_EXPIRED', error: 'gone' } },
    })
    const w = makeWrapper()
    await flushPromises()
    await w.find('input').setValue('123456')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(replace).toHaveBeenCalledWith({ name: 'login', query: { expired: '1' } })
  })
})
