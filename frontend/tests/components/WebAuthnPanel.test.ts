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

async function register(w: ReturnType<typeof mount>) {
  await w.findAll('button').find((b) => b.text() === en.webauthn.add_cta)!.trigger('click')
  const inputs = w.findAll('.add-form input')
  await inputs[0].setValue('YubiKey')
  await inputs[1].setValue('pw')
  await w.findAll('button').find((b) => b.text() === en.webauthn.register)!.trigger('click')
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
