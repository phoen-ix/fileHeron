/* The reveal card (v2.24.0): nothing is revealed until the reader clicks, the
 * content renders as TEXT, a wrong passphrase is shown in the card, and the
 * last view says so. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'
import SecretReveal from '@/components/SecretReveal.vue'
import type { RevealSecretResponse } from '@/types/api'

function mountCard(props: {
  requiresPassphrase: boolean
  viewsLeft: number | null
  reveal: (passphrase: string | null) => Promise<RevealSecretResponse>
}) {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(SecretReveal, { props, global: { plugins: [i18n] } })
}

beforeEach(() => setActivePinia(createPinia()))

describe('SecretReveal', () => {
  it('reveals nothing until the reader clicks', async () => {
    const reveal = vi.fn()
    const w = mountCard({ requiresPassphrase: false, viewsLeft: 3, reveal })
    await flushPromises()
    expect(reveal).not.toHaveBeenCalled()
    expect(w.find('[data-testid="secret-content"]').exists()).toBe(false)
  })

  it('renders the content as text, never as markup', async () => {
    const hostile = '<img src=x onerror="alert(1)">'
    const reveal = vi.fn().mockResolvedValue({ content: hostile, views_left: 2, ended: false })
    const w = mountCard({ requiresPassphrase: false, viewsLeft: 3, reveal })
    await w.find('[data-testid="reveal-button"]').trigger('submit')
    await flushPromises()
    expect(reveal).toHaveBeenCalledWith(null)
    const box = w.find('[data-testid="secret-content"]')
    expect(box.text()).toBe(hostile)
    expect(box.find('img').exists()).toBe(false)
    expect(w.emitted('revealed')).toHaveLength(1)
  })

  it('sends the passphrase it was given and shows a wrong one in the card', async () => {
    const reveal = vi.fn().mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 403,
        data: {
          error: 'That passphrase is not correct.',
          code: 'SECRET_PASSPHRASE_INVALID',
          details: {},
          request_id: 'r1',
        },
      },
    })
    const w = mountCard({ requiresPassphrase: true, viewsLeft: 1, reveal })
    expect(w.find('[data-testid="last-view-warning"]').exists()).toBe(true)
    expect(w.find('[data-testid="reveal-button"]').attributes('disabled')).toBeDefined()
    await w.find('[data-testid="reveal-passphrase"]').setValue('guess')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(reveal).toHaveBeenCalledWith('guess')
    expect(w.find('[role="alert"]').text()).toContain(en.errors.SECRET_PASSPHRASE_INVALID)
    expect(w.emitted('failed')?.[0]).toEqual(['SECRET_PASSPHRASE_INVALID'])
  })

  it('says when that was the last view', async () => {
    const reveal = vi.fn().mockResolvedValue({ content: 'x', views_left: 0, ended: true })
    const w = mountCard({ requiresPassphrase: false, viewsLeft: 1, reveal })
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.find('[data-testid="reveal-gone"]').text()).toBe(en.secrets.reveal.destroyed)
  })
})
