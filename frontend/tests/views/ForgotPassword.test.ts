/* forgotPassword() had no catch: a 429 RATE_LIMITED, a 5xx or a dropped
 * connection just re-enabled the button, so the user retried - deepening the
 * rate limit - with nothing on screen and an unhandled rejection. */
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const forgotPassword = vi.fn()
vi.mock('@/api/auth', () => ({ forgotPassword: (p: unknown) => forgotPassword(p) }))

import ForgotPassword from '@/views/ForgotPassword.vue'

function makeWrapper() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(ForgotPassword, {
    global: { plugins: [i18n], stubs: { RouterLink: true, AuthCanvas: { template: '<div><slot /></div>' } } },
  })
}

describe('ForgotPassword', () => {
  it('shows a refusal instead of silently re-enabling the button', async () => {
    forgotPassword.mockRejectedValueOnce(
      Object.assign(new Error('429'), {
        isAxiosError: true,
        response: { status: 429, data: { code: 'RATE_LIMITED', error: 'Too many' } },
      }),
    )
    const w = makeWrapper()
    await w.find('input[type="email"]').setValue('a@b.test')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.find('[role="alert"]').text()).toBe(en.errors.RATE_LIMITED)
  })

  it('still confirms on success', async () => {
    forgotPassword.mockResolvedValueOnce({ data: { ok: true } })
    const w = makeWrapper()
    await w.find('input[type="email"]').setValue('a@b.test')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.text()).toContain(en.forgot.sent_title)
  })
})
