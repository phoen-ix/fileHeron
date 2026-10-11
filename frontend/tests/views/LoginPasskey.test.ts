/* Passkey sign-in on the login page (v2.27.0): the "Sign in with a passkey"
 * button and the email field's autofill appear only while an admin has the
 * switch on, and WEBAUTHN_REQUIRED - the password was right, the passkey
 * finishes it - leads to a passkey step instead of an error. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'
import { useSiteStore } from '@/stores/site'

type PasskeyOnlyOpts = {
  mediation?: CredentialMediationRequirement
  signal?: AbortSignal
  onPicked?: () => void
}

const login = vi.fn()
const loginWithPasskey = vi.fn()
const loginWithPasskeyOnly = vi.fn<(opts?: PasskeyOnlyOpts) => Promise<unknown>>()
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    login,
    loginWithPasskey,
    loginWithPasskeyOnly,
    loginWithRecovery: vi.fn(),
    user: null,
  }),
}))

let supported = true
let conditional = false
vi.mock('@/composables/useWebAuthn', () => ({
  isWebAuthnSupported: () => supported,
  isConditionalMediationAvailable: async () => conditional,
}))

const replace = vi.fn(async () => {})
const push = vi.fn(async () => {})
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {}, path: '/login' }),
  useRouter: () => ({ replace, push }),
}))

import Login from '@/views/Login.vue'

function envelope(code: string) {
  return { isAxiosError: true, response: { status: 401, data: { code, error: code } } }
}

/** A conditional request that waits until it is aborted, as the browser's does. */
function waitUntilAborted(opts?: PasskeyOnlyOpts) {
  return new Promise((_resolve, reject) => {
    opts?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  })
}

function makeWrapper(passkeySignin: boolean) {
  setActivePinia(createPinia())
  useSiteStore().passkeySignin = passkeySignin
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(Login, { global: { plugins: [i18n], stubs: { RouterLink: true } } })
}

describe('Login with passkey sign-in', () => {
  beforeEach(() => {
    login.mockReset()
    loginWithPasskey.mockReset()
    loginWithPasskeyOnly.mockReset()
    push.mockClear()
    supported = true
    conditional = false
  })

  it('offers nothing new while the switch is off', async () => {
    conditional = true
    const w = makeWrapper(false)
    await flushPromises()

    expect(w.find('[data-testid="passkey-signin"]').exists()).toBe(false)
    expect(w.find('#login-email').attributes('autocomplete')).toBe('username')
    expect(loginWithPasskeyOnly).not.toHaveBeenCalled()
  })

  it('offers nothing to a browser without passkeys', async () => {
    supported = false
    const w = makeWrapper(true)
    await flushPromises()

    expect(w.find('[data-testid="passkey-signin"]').exists()).toBe(false)
  })

  it('signs in with a passkey alone from the button', async () => {
    loginWithPasskeyOnly.mockResolvedValueOnce({})
    const w = makeWrapper(true)
    await flushPromises()

    expect(w.find('#login-email').attributes('autocomplete')).toBe('username webauthn')
    await w.find('[data-testid="passkey-signin"]').trigger('click')
    await flushPromises()

    // A modal request: no conditional mediation, nothing to abort.
    expect(loginWithPasskeyOnly).toHaveBeenCalledWith()
    expect(push).toHaveBeenCalledWith('/')
  })

  it('arms the autofill on arrival and aborts it on leaving', async () => {
    conditional = true
    loginWithPasskeyOnly.mockImplementation(waitUntilAborted)
    const w = makeWrapper(true)
    await flushPromises()

    expect(loginWithPasskeyOnly).toHaveBeenCalledTimes(1)
    const opts = loginWithPasskeyOnly.mock.calls[0][0]!
    expect(opts.mediation).toBe('conditional')
    expect(opts.signal?.aborted).toBe(false)

    w.unmount()
    expect(opts.signal?.aborted).toBe(true)
  })

  it('aborts the autofill before the button starts its own request', async () => {
    conditional = true
    loginWithPasskeyOnly.mockImplementationOnce(waitUntilAborted)
    loginWithPasskeyOnly.mockResolvedValueOnce({})
    const w = makeWrapper(true)
    await flushPromises()
    const autofill = loginWithPasskeyOnly.mock.calls[0][0]!

    await w.find('[data-testid="passkey-signin"]').trigger('click')
    await flushPromises()

    expect(autofill.signal?.aborted).toBe(true)
    expect(loginWithPasskeyOnly).toHaveBeenLastCalledWith()
  })

  it('does not retry a refused start in a loop', async () => {
    // Nothing was picked - the switch went off, the IP is rate limited, or the
    // browser refused outright. Retrying at once would hammer the server.
    conditional = true
    loginWithPasskeyOnly.mockRejectedValue(envelope('RATE_LIMITED'))
    const w = makeWrapper(true)
    await flushPromises()

    expect(loginWithPasskeyOnly).toHaveBeenCalledTimes(1)
    expect(w.find('[role="alert"]').exists()).toBe(false)
  })

  it('shows a failed pick and offers the passkeys again', async () => {
    conditional = true
    loginWithPasskeyOnly.mockImplementationOnce(async (opts) => {
      opts?.onPicked?.()
      throw envelope('PASSKEY_SIGNIN_FAILED')
    })
    loginWithPasskeyOnly.mockImplementation(waitUntilAborted)
    const w = makeWrapper(true)
    await flushPromises()

    expect(w.find('[role="alert"]').text()).toBe(en.errors.PASSKEY_SIGNIN_FAILED)
    expect(loginWithPasskeyOnly).toHaveBeenCalledTimes(2)
  })

  it('finishes a password sign-in with the passkey when the server asks for it', async () => {
    login.mockRejectedValueOnce(envelope('WEBAUTHN_REQUIRED'))
    loginWithPasskey.mockResolvedValueOnce({ status: 'ok' })
    const w = makeWrapper(true)
    await flushPromises()

    await w.find('#login-email').setValue('pk@example.com')
    await w.find('#login-password').setValue('pw')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(w.find('[data-testid="passkey-step"]').exists()).toBe(true)
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(w.find('button[type="submit"]').text()).toContain(en.login.passkey_continue)

    await w.find('form').trigger('submit')
    await flushPromises()

    expect(loginWithPasskey).toHaveBeenCalledWith('pk@example.com', 'pw')
    expect(push).toHaveBeenCalledWith('/')
  })

  it('explains a passkey step this browser cannot take', async () => {
    supported = false
    login.mockRejectedValueOnce(envelope('WEBAUTHN_REQUIRED'))
    const w = makeWrapper(true)
    await flushPromises()

    await w.find('#login-email').setValue('pk@example.com')
    await w.find('#login-password').setValue('pw')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(w.find('[data-testid="passkey-step"]').exists()).toBe(false)
    expect(w.find('[role="alert"]').text()).toBe(en.login.passkey_unsupported)
  })
})
