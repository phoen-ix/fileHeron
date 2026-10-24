/* The three emailed-link pages (verify email, confirm and cancel an email
 * change) showed a bare "-" as their heading when the link failed: a
 * placeholder left by the repo-wide dash replacement (v1.27.2). Each now
 * names what failed, above the server's reason. */
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const refused = () =>
  Promise.reject(
    Object.assign(new Error('400'), {
      isAxiosError: true,
      response: { status: 400, data: { code: 'INVALID_TOKEN', error: 'This link is not valid.' } },
    }),
  )

vi.mock('@/api/auth', () => ({
  verifyEmail: () => refused(),
  confirmEmailChange: () => refused(),
  cancelEmailChange: () => refused(),
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { token: 'spent' } }) }))

import CancelEmailChange from '@/views/CancelEmailChange.vue'
import ConfirmEmailChange from '@/views/ConfirmEmailChange.vue'
import EmailVerify from '@/views/EmailVerify.vue'

describe.each([
  ['EmailVerify', EmailVerify, en.verify.error_title],
  ['ConfirmEmailChange', ConfirmEmailChange, en.confirm_email_change.error_title],
  ['CancelEmailChange', CancelEmailChange, en.cancel_email_change.error_title],
])('%s with a failed link', (_name, view, heading) => {
  it('heads the error with what failed, not a dash', async () => {
    const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
    const w = mount(view, {
      global: { plugins: [i18n], stubs: { RouterLink: true, AuthCanvas: { template: '<div><slot /></div>' } } },
    })
    await flushPromises()
    expect(w.find('[role="alert"]').exists()).toBe(true)
    expect(w.find('h1').text()).toBe(heading)
  })
})
