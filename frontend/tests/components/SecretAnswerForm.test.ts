/* The answer form for a secret request (v2.24.0), shared by the signed-in
 * answer page and the anonymous /r page: submit is derived from visible
 * blockers, an own passphrase must be typed twice, and the text is cleared the
 * moment it is sent. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'
import SecretAnswerForm from '@/components/SecretAnswerForm.vue'

function mountForm(submit = vi.fn().mockResolvedValue(undefined), hasRequestPassphrase = false) {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(SecretAnswerForm, {
    props: { requesterName: 'Ann', hasRequestPassphrase, submit },
    global: { plugins: [i18n], stubs: { PasswordGenerator: true } },
  })
}

beforeEach(() => setActivePinia(createPinia()))

describe('SecretAnswerForm', () => {
  it('cannot be sent empty, and says why', () => {
    const w = mountForm()
    expect(w.find('[data-testid="answer-submit"]').attributes('disabled')).toBeDefined()
    expect(w.text()).toContain(en.secrets.create.blockers.no_content)
  })

  it('sends the text and an own passphrase, then forgets both', async () => {
    const submit = vi.fn().mockResolvedValue(undefined)
    const w = mountForm(submit)
    await w.find('[data-testid="answer-content"]').setValue('hunter2')
    await w.find('[data-testid="answer-passphrase"]').setValue('mine12345')
    expect(w.find('[data-testid="answer-submit"]').attributes('disabled')).toBeDefined()
    await w.find('[data-testid="answer-passphrase-repeat"]').setValue('mine12345')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(submit).toHaveBeenCalledWith('hunter2', 'mine12345')
    expect(w.emitted('sent')).toHaveLength(1)
    expect((w.find('[data-testid="answer-content"]').element as HTMLTextAreaElement).value).toBe('')
  })

  it('keeps the text and shows the error when sending fails', async () => {
    const submit = vi.fn().mockRejectedValue(new Error('offline'))
    const w = mountForm(submit)
    await w.find('[data-testid="answer-content"]').setValue('hunter2')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.emitted('sent')).toBeUndefined()
    expect(w.find('[role="alert"]').exists()).toBe(true)
    expect((w.find('[data-testid="answer-content"]').element as HTMLTextAreaElement).value).toBe(
      'hunter2',
    )
  })

  it('says only the requester can open it, and when their passphrase guards it', () => {
    expect(mountForm().text()).toContain('Only Ann will be able to open what you send.')
    expect(mountForm(vi.fn(), true).text()).toContain('with a passphrase they chose')
  })
})
