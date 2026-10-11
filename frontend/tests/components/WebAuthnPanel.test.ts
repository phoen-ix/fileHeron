/* A browser refusing an authenticator that is already registered raises an
 * InvalidStateError DOMException. describe() only understands API envelopes, so
 * it read "Something went wrong"; the DOMException's own message was the dead
 * right-hand side of `describe(err) || err.message`. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

vi.mock('@/api/webauthn', () => ({
  listCredentials: vi.fn(async () => ({ data: { items: [] } })),
  registerBegin: vi.fn(async () => ({ data: { options: { challenge: 'c', rp: { id: 'x', name: 'x' }, user: { id: 'u', name: 'u', displayName: 'u' }, pubKeyCredParams: [] } } })),
  registerComplete: vi.fn(),
  deleteCredential: vi.fn(),
}))
const performRegistration = vi.fn()
vi.mock('@/composables/useWebAuthn', () => ({
  isWebAuthnSupported: () => true,
  performRegistration: (o: unknown) => performRegistration(o),
}))

import WebAuthnPanel from '@/components/WebAuthnPanel.vue'

async function register(w: ReturnType<typeof mount>, password = 'pw') {
  await w.findAll('button').find((b) => b.text() === en.webauthn.add_cta)!.trigger('click')
  const inputs = w.findAll('.add-form input')
  expect(inputs).toHaveLength(1) // the name; the password is asked in the dialog
  await inputs[0].setValue('YubiKey')
  await w.find('[data-testid="webauthn-register"]').trigger('click')
  await w.find('[data-testid="step-up-password"]').setValue(password)
  await w.find('[data-testid="step-up-dialog"] form').trigger('submit')
  await flushPromises()
}

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(WebAuthnPanel, { global: { plugins: [i18n] } })
}

describe('WebAuthnPanel registration errors', () => {
  beforeEach(() => performRegistration.mockReset())

  it('names an already-registered authenticator', async () => {
    performRegistration.mockRejectedValueOnce(new DOMException('dup', 'InvalidStateError'))
    const w = makeWrapper()
    await flushPromises()
    await register(w)
    expect(w.find('[role="alert"]').text()).toBe(en.webauthn.already_registered)
  })

  it("shows another browser-side failure's own message", async () => {
    performRegistration.mockRejectedValueOnce(new DOMException('The operation timed out.', 'TimeoutError'))
    const w = makeWrapper()
    await flushPromises()
    await register(w)
    expect(w.find('[role="alert"]').text()).toBe('The operation timed out.')
  })
})

describe('WebAuthnPanel asks for the password in a dialog', () => {
  beforeEach(() => performRegistration.mockReset())

  it('sends it to register/begin, then closes before the browser prompt', async () => {
    const api = await import('@/api/webauthn')
    const w = makeWrapper()
    await flushPromises()
    await register(w, 'secret')
    expect(api.registerBegin).toHaveBeenCalledWith('secret')
    expect(performRegistration).toHaveBeenCalled()
    expect(w.find('[data-testid="step-up-dialog"]').exists()).toBe(false)
  })

  it('a wrong password stays in the dialog and no passkey prompt starts', async () => {
    const api = await import('@/api/webauthn')
    vi.mocked(api.registerBegin).mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 403, data: { code: 'INVALID_PASSWORD', error: 'Password incorrect.' } },
    })
    const w = makeWrapper()
    await flushPromises()
    await register(w, 'nope')
    const dialog = w.find('[data-testid="step-up-dialog"]')
    expect(dialog.exists()).toBe(true)
    expect(dialog.find('[role="alert"]').exists()).toBe(true)
    expect(performRegistration).not.toHaveBeenCalled()
  })
})

describe('WebAuthnPanel under passkey sign-in', () => {
  const item = (id: number, discoverable: boolean | null) => ({
    id,
    name: `key ${id}`,
    transports: [],
    created_at: '2026-10-01T00:00:00',
    last_used_at: null,
    discoverable,
  })

  async function mountWith(passkeySignin: boolean) {
    const api = await import('@/api/webauthn')
    vi.mocked(api.listCredentials).mockResolvedValueOnce({
      data: { items: [item(1, true), item(2, false), item(3, null)] },
    } as never)
    setActivePinia(createPinia())
    const { useSiteStore } = await import('@/stores/site')
    useSiteStore().passkeySignin = passkeySignin
    const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
    const w = mount(WebAuthnPanel, { global: { plugins: [i18n] } })
    await flushPromises()
    return w
  }

  it('marks the passkeys that sign in on their own, and says what is at stake', async () => {
    const w = await mountWith(true)

    const badges = w.findAll('[data-testid="passkey-signs-in-alone"]')
    expect(badges).toHaveLength(1)
    expect(badges[0].text()).toBe(en.webauthn.signs_in_alone)
    expect(w.text()).toContain(en.webauthn.signin_note)
  })

  it('says nothing about it while the switch is off', async () => {
    const w = await mountWith(false)

    expect(w.find('[data-testid="passkey-signs-in-alone"]').exists()).toBe(false)
    expect(w.text()).not.toContain(en.webauthn.signin_note)
  })
})
