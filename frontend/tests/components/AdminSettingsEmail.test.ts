/* The email settings view had no component test at all, so the step-up change
 * would have shipped with zero regression cover on the one surface an admin
 * actually touches. These pin the two behaviours that matter: the everyday
 * test must stay promptless, and the refusal must reveal the password field
 * rather than surfacing a raw error. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const SETTINGS = {
  host: 'mail.corp.local',
  port: 587,
  user: 'postmaster@corp.local',
  is_password_set: true,
  from_email: 'noreply@corp.local',
  from_name: 'file:Heron',
  tls_mode: 'starttls',
  helo_hostname: '',
  has_overrides: true,
  allow_anonymous: false,
}

const getEmailSettings = vi.fn(async () => ({ data: SETTINGS }))
const updateEmailSettings = vi.fn(async (p: unknown) => ({ data: { ...SETTINGS, ...(p as object) } }))
const testEmailSend = vi.fn(async (_p?: unknown) => ({
  data: { ok: true, error_class: null, error_message: null, smtp_code: null, hint: null },
}))

vi.mock('@/api/admin', () => ({
  getEmailSettings: () => getEmailSettings(),
  updateEmailSettings: (p: unknown) => updateEmailSettings(p),
  testEmailSend: (p: unknown) => testEmailSend(p),
}))

const pushToast = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast }) }))

import AdminSettingsEmail from '@/views/AdminSettingsEmail.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSettingsEmail, { global: { plugins: [i18n] } })
}

type Wrapper = ReturnType<typeof makeWrapper>

/** The view has two type=email inputs (the From address and the test
 *  recipient); pick the latter by its placeholder. */
function recipient(w: Wrapper) {
  return w.findAll('input[type="email"]').filter((i) =>
    (i.attributes('placeholder') ?? '').includes('Recipient'),
  )[0]
}

function testButton(w: Wrapper) {
  return w.findAll('button').filter((b) => b.text().includes('Send test email'))[0]
}

function stepUpRefusal() {
  return {
    isAxiosError: true,
    response: { status: 403, data: { code: 'STEP_UP_REQUIRED', error: 'nope' } },
  }
}

describe('AdminSettingsEmail', () => {
  beforeEach(() => {
    getEmailSettings.mockClear()
    updateEmailSettings.mockClear()
    testEmailSend.mockClear()
    pushToast.mockClear()
    testEmailSend.mockImplementation(async (_p?: unknown) => ({
      data: { ok: true, error_class: null, error_message: null, smtp_code: null, hint: null },
    }))
  })

  it('loads the saved config into the form', async () => {
    const w = makeWrapper()
    await flushPromises()
    // v-model values live on the element, not in the rendered markup.
    const host = w.find('input[placeholder="smtp.example.com"]')
      .element as HTMLInputElement
    expect(host.value).toBe('mail.corp.local')
  })

  it('does not ask for a password on an ordinary test', async () => {
    const w = makeWrapper()
    await flushPromises()
    await recipient(w).setValue('ops@example.com')
    await testButton(w).trigger('click')
    await flushPromises()

    expect(testEmailSend).toHaveBeenCalled()
    expect(w.find('input[type="password"][autocomplete="current-password"]').exists()).toBe(false)
  })

  it('reveals the password field when the server asks for re-auth', async () => {
    testEmailSend.mockRejectedValueOnce(stepUpRefusal())
    const w = makeWrapper()
    await flushPromises()
    await recipient(w).setValue('ops@example.com')
    await testButton(w).trigger('click')
    await flushPromises()

    const field = w.find('input[type="password"][autocomplete="current-password"]')
    expect(field.exists()).toBe(true)
    // The refusal is explained, not surfaced as a raw failed-test result.
    expect(w.text()).toContain('Confirm your password')
  })

  it('sends the confirmation password on the retry', async () => {
    testEmailSend.mockRejectedValueOnce(stepUpRefusal())
    const w = makeWrapper()
    await flushPromises()
    await recipient(w).setValue('ops@example.com')
    await testButton(w).trigger('click')
    await flushPromises()

    await w.find('input[type="password"][autocomplete="current-password"]').setValue('my-own-password')
    await testButton(w).trigger('click')
    await flushPromises()

    const calls = testEmailSend.mock.calls
    const lastCall = calls[calls.length - 1][0] as Record<string, unknown>
    expect(lastCall.confirm_password).toBe('my-own-password')
  })
})
