/* /r#<token> (v2.24.0): the token comes from the URL FRAGMENT and travels in a
 * POST body; opening the page answers nothing; a closed request says why; a
 * link without its fragment is reported without calling the API. */
import { flushPromises, mount } from '@vue/test-utils'
import { AxiosError, AxiosHeaders } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ peekSecretRequest: vi.fn(), answerPublicSecretRequest: vi.fn() }))
const route = vi.hoisted(() => ({ hash: '#tok-123' }))
vi.mock('@/api/secretRequests', () => ({
  peekSecretRequest: (t: string) => api.peekSecretRequest(t),
  answerPublicSecretRequest: (t: string, c: string, p: string | null) =>
    api.answerPublicSecretRequest(t, c, p),
}))
vi.mock('vue-router', () => ({ useRoute: () => route }))

import PublicSecretRequest from '@/views/PublicSecretRequest.vue'

function mountView() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(PublicSecretRequest, {
    global: {
      plugins: [i18n],
      stubs: { BrandLogo: true, BrandMark: true, PasswordGenerator: true },
    },
  })
}

function closed(reason: string) {
  const headers = new AxiosHeaders()
  return new AxiosError('gone', 'ERR_BAD_REQUEST', { headers }, null, {
    status: 410,
    statusText: 'Gone',
    headers: {},
    config: { headers },
    data: { error: 'closed', code: 'SECRET_REQUEST_CLOSED', details: { reason } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.peekSecretRequest.mockReset()
  api.answerPublicSecretRequest.mockReset()
  route.hash = '#tok-123'
  api.peekSecretRequest.mockResolvedValue({
    data: {
      requester_name: 'Ann',
      label: 'Router password',
      note: 'The admin one.',
      expires_at: '2030-01-01T10:00:00',
      answer_max_views: 1,
      answer_expires_in_sec: 604800,
      has_passphrase: true,
    },
  })
})

describe('PublicSecretRequest', () => {
  it('peeks with the fragment token and answers nothing on load', async () => {
    const w = mountView()
    await flushPromises()
    expect(api.peekSecretRequest).toHaveBeenCalledWith('tok-123')
    expect(api.answerPublicSecretRequest).not.toHaveBeenCalled()
    expect(w.text()).toContain('Router password')
    expect(w.find('[data-testid="public-request-note"]').text()).toBe('The admin one.')
    expect(w.text()).toContain('with a passphrase they chose')
  })

  it('answers once and says the requester was told', async () => {
    api.answerPublicSecretRequest.mockResolvedValue({ data: { ok: true, requester_name: 'Ann' } })
    const w = mountView()
    await flushPromises()
    await w.find('[data-testid="answer-content"]').setValue('hunter2')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(api.answerPublicSecretRequest).toHaveBeenCalledWith('tok-123', 'hunter2', null)
    expect(w.find('[data-testid="public-answer-sent"]').text()).toContain('Ann has been told')
    expect(w.find('[data-testid="secret-answer-form"]').exists()).toBe(false)
  })

  it('a closed request says why', async () => {
    api.peekSecretRequest.mockRejectedValue(closed('fulfilled'))
    const w = mountView()
    await flushPromises()
    const box = w.find('[data-testid="public-request-error"]')
    expect(box.text()).toContain(en.public_secret_request.closed_title)
    expect(box.text()).toContain(en.secret_requests.closed.fulfilled)
  })

  it('a link without its fragment is reported, not sent anywhere', async () => {
    route.hash = ''
    const w = mountView()
    await flushPromises()
    expect(api.peekSecretRequest).not.toHaveBeenCalled()
    expect(w.text()).toContain(en.public_secret_request.incomplete)
  })
})
