/* The interstitial is the only place the second factor is demanded for an SSO
 * or passkey login, so it needs cover in its own right - the backend can be
 * perfect and this view can still strand the user. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const completeSecondFactor = vi.fn(async () => ({}))
const completeSecondFactorWithPasskey = vi.fn(async (_pending: string) => ({}))
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ completeSecondFactor, completeSecondFactorWithPasskey }),
}))

let passkeysSupported = true
vi.mock('@/composables/useWebAuthn', () => ({
  isWebAuthnSupported: () => passkeysSupported,
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
    completeSecondFactorWithPasskey.mockReset()
    replace.mockClear()
    query = { pending: 'pending-token-abc' }
    passkeysSupported = true
    completeSecondFactor.mockImplementation(async () => ({}))
    completeSecondFactorWithPasskey.mockImplementation(async () => ({}))
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

  it('takes a code only when the server names no methods (an older server)', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(w.find('form').exists()).toBe(true)
    expect(w.find('[data-testid="twofa-passkey"]').exists()).toBe(false)
  })

  it('offers only the passkey when that is all the account has', async () => {
    query = { pending: 'pending-token-abc', methods: 'passkey' }
    const w = makeWrapper()
    await flushPromises()

    expect(w.find('form').exists()).toBe(false)
    const button = w.find('[data-testid="twofa-passkey"]')
    expect(button.text()).toBe(en.login_2fa.use_passkey)
    await button.trigger('click')
    await flushPromises()

    expect(completeSecondFactorWithPasskey).toHaveBeenCalledWith('pending-token-abc')
    expect(replace).toHaveBeenLastCalledWith({ name: 'home' })
  })

  it('offers both when the account has both', async () => {
    query = { pending: 'pending-token-abc', methods: 'totp,passkey' }
    const w = makeWrapper()
    await flushPromises()

    expect(w.find('form').exists()).toBe(true)
    expect(w.find('[data-testid="twofa-passkey"]').text()).toBe(en.login_2fa.use_passkey_instead)
  })

  it('tells a browser without passkeys why it cannot continue', async () => {
    passkeysSupported = false
    query = { pending: 'pending-token-abc', methods: 'passkey' }
    const w = makeWrapper()
    await flushPromises()

    expect(w.find('[data-testid="twofa-passkey"]').exists()).toBe(false)
    expect(w.find('[role="alert"]').text()).toBe(en.login_2fa.passkey_unsupported)
  })

  it('sends the user back to login when the pending token expired mid-passkey', async () => {
    query = { pending: 'pending-token-abc', methods: 'passkey' }
    completeSecondFactorWithPasskey.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 401, data: { code: 'PENDING_2FA_EXPIRED', error: 'gone' } },
    })
    const w = makeWrapper()
    await flushPromises()
    await w.find('[data-testid="twofa-passkey"]').trigger('click')
    await flushPromises()

    expect(replace).toHaveBeenCalledWith({ name: 'login', query: { expired: '1' } })
  })
})
