/* One secret request (v2.24.0): someone asked answers on this page, a closed
 * request says why instead of offering the form, and the requester gets the way
 * to the answer and Cancel - never an answer's text. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'
import type { SecretRequestResponse } from '@/types/api'

const api = vi.hoisted(() => ({
  getSecretRequest: vi.fn(),
  answerSecretRequest: vi.fn(),
  cancelSecretRequest: vi.fn(),
  getSecretRequestLinks: vi.fn(),
}))
vi.mock('@/api/secretRequests', () => ({
  getSecretRequest: (id: string) => api.getSecretRequest(id),
  answerSecretRequest: (id: string, c: string, p: string | null) =>
    api.answerSecretRequest(id, c, p),
  cancelSecretRequest: (id: string) => api.cancelSecretRequest(id),
  getSecretRequestLinks: (id: string) => api.getSecretRequestLinks(id),
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'r1' } }) }))

import SecretRequestDetail from '@/views/SecretRequestDetail.vue'

function request(over: Partial<SecretRequestResponse> = {}): SecretRequestResponse {
  return {
    id: 'r1',
    state: 'open',
    closed_reason: null,
    label: 'Router password',
    note: 'The admin one.',
    requester: { id: 1, display_name: 'Ann' },
    created_at: '2030-01-01T09:00:00',
    expires_at: '2030-01-08T09:00:00',
    ended_at: null,
    answer_max_views: 1,
    answer_expires_in_sec: 604800,
    has_passphrase: false,
    viewer_role: 'target',
    can_answer: true,
    targets: [],
    ...over,
  }
}

function mountView() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(SecretRequestDetail, {
    global: {
      plugins: [i18n],
      stubs: {
        RouterLink: { props: ['to'], template: '<a class="router-link"><slot /></a>' },
        PasswordGenerator: true,
      },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  for (const fn of Object.values(api)) fn.mockReset()
})

describe('SecretRequestDetail', () => {
  it('lets someone asked answer, then says it was sent', async () => {
    api.getSecretRequest.mockResolvedValue({ data: request() })
    api.answerSecretRequest.mockResolvedValue({ data: { ok: true, requester_name: 'Ann' } })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="request-note-text"]').text()).toBe('The admin one.')
    await w.find('[data-testid="answer-content"]').setValue('hunter2')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(api.answerSecretRequest).toHaveBeenCalledWith('r1', 'hunter2', null)
    expect(w.find('[data-testid="answer-sent"]').text()).toContain('Ann has been told')
  })

  it('a closed request says why instead of offering the form', async () => {
    api.getSecretRequest.mockResolvedValue({
      data: request({ state: 'fulfilled', closed_reason: 'fulfilled', can_answer: false }),
    })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="secret-answer-form"]').exists()).toBe(false)
    expect(w.find('[data-testid="request-closed"]').text()).toBe(
      en.secret_requests.closed.fulfilled,
    )
  })

  it('shows the requester the way to the answer, and Cancel only while open', async () => {
    api.getSecretRequest.mockResolvedValue({
      data: request({
        viewer_role: 'requester',
        can_answer: false,
        state: 'fulfilled',
        closed_reason: 'fulfilled',
        answer_secret_id: 's1',
        answered_via: 'user',
        answered_by: { id: 2, display_name: 'Ben' },
        fulfilled_at: '2030-01-02T09:00:00',
        targets: [{ id: 1, kind: 'user', user: { id: 2, display_name: 'Ben' } }],
      }),
    })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="open-answer"]').exists()).toBe(true)
    expect(w.find('[data-testid="cancel-request"]').exists()).toBe(false)
    expect(w.find('[data-testid="secret-answer-form"]').exists()).toBe(false)
    expect(w.text()).toContain('Ben')
  })
})
