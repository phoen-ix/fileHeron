/* The secret detail page (v2.24.0). Pins a bug the e2e journey caught: after
 * the LAST view the page re-reads the secret, `can_reveal` turns false, and a
 * plain `v-if` swapped the reveal card out - unmounting it, and with it the text
 * the reader had just revealed. The card now stays mounted once it revealed. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  getSecret: vi.fn(),
  revealSecret: vi.fn(),
  burnSecret: vi.fn(),
  getSecretLinks: vi.fn(),
  replaceSecretLink: vi.fn(),
  removeSecretLink: vi.fn(),
}))
vi.mock('@/api/secrets', () => ({
  getSecret: (id: string) => api.getSecret(id),
  revealSecret: (id: string, p: string | null) => api.revealSecret(id, p),
  burnSecret: (id: string) => api.burnSecret(id),
  getSecretLinks: (id: string) => api.getSecretLinks(id),
  replaceSecretLink: (id: string) => api.replaceSecretLink(id),
  removeSecretLink: (id: string) => api.removeSecretLink(id),
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'sec1' } }) }))

import { useAuthStore } from '@/stores/auth'
import type { MeResponse, SecretResponse } from '@/types/api'
import SecretDetail from '@/views/SecretDetail.vue'

function secret(over: Partial<SecretResponse> = {}): SecretResponse {
  return {
    id: 'sec1',
    state: 'active',
    label: 'VPN',
    sender: { id: 2, display_name: 'Ann' },
    created_at: '2026-10-01T10:00:00',
    ended_at: null,
    expires_at: null,
    max_views: 1,
    view_scope: 'per_person',
    views_used: 0,
    has_passphrase: false,
    notify_on_view: false,
    burn_after_failures: null,
    viewer_role: 'recipient',
    my_views_left: 1,
    can_reveal: true,
    still_recipient: true,
    burned_for_me: false,
    my_failed_attempts: 0,
    recipients: [],
    events: [],
    ...over,
  }
}

function mountView() {
  const auth = useAuthStore()
  auth.user = { id: 1, role: 'employee', display_name: 'Ben' } as MeResponse
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(SecretDetail, { global: { plugins: [i18n], stubs: { RouterLink: true } } })
}

beforeEach(() => {
  setActivePinia(createPinia())
  for (const fn of Object.values(api)) fn.mockReset()
})

describe('SecretDetail', () => {
  it('keeps the revealed text on screen after the last view ends the secret', async () => {
    api.getSecret
      .mockResolvedValueOnce({ data: secret() })
      .mockResolvedValue({
        data: secret({ state: 'burned', can_reveal: false, my_views_left: 0, views_used: 1 }),
      })
    api.revealSecret.mockResolvedValue({
      data: { content: 'hunter2', views_left: 0, ended: true },
    })
    const w = mountView()
    await flushPromises()
    await w.find('[data-testid="reveal-button"]').trigger('submit')
    await flushPromises()

    expect(api.getSecret).toHaveBeenCalledTimes(2) // the reload after the reveal ran
    expect(w.find('[data-testid="secret-content"]').text()).toBe('hunter2')
    expect(w.find('[data-testid="reveal-gone"]').exists()).toBe(true)
    expect(w.find('[data-testid="not-revealable"]').exists()).toBe(false)
  })

  it('says why when it cannot be revealed', async () => {
    api.getSecret.mockResolvedValue({
      data: secret({ can_reveal: false, burned_for_me: true }),
    })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="secret-reveal"]').exists()).toBe(false)
    expect(w.find('[data-testid="not-revealable"]').text()).toBe(
      en.secrets.detail.burned_for_me,
    )
  })

  it('shows the sender the status and no reveal button', async () => {
    api.getSecret.mockResolvedValue({
      data: secret({
        viewer_role: 'sender',
        can_reveal: false,
        recipients: [
          {
            id: 1,
            kind: 'user',
            user: { id: 1, display_name: 'Ben' },
            views_used: 1,
            views_left: 0,
            failed_attempts: 0,
            locked_until: null,
            burned: false,
            revoked: false,
            emailed_at: null,
            created_at: '2026-10-01T10:00:00',
            members: [],
          },
        ],
      }),
    })
    const auth = useAuthStore()
    const w = mountView()
    auth.user = { id: 2, role: 'employee', display_name: 'Ann' } as MeResponse
    await flushPromises()
    expect(w.find('[data-testid="secret-reveal"]').exists()).toBe(false)
    expect(w.find('[data-testid="burn-now"]').exists()).toBe(true)
    expect(w.text()).toContain('Ben')
  })
})
