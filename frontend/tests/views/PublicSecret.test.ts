/* /s#<token> (v2.24.0): the token comes from the URL FRAGMENT and travels in a
 * POST body; opening the page costs nothing, only Reveal uses a view; a link
 * without its fragment says so without calling the API. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ peekSecret: vi.fn(), revealPublicSecret: vi.fn() }))
const route = vi.hoisted(() => ({ hash: '#tok-123' }))
vi.mock('@/api/secrets', () => ({
  peekSecret: (t: string) => api.peekSecret(t),
  revealPublicSecret: (t: string, p: string | null) => api.revealPublicSecret(t, p),
}))
vi.mock('vue-router', () => ({ useRoute: () => route }))

import PublicSecret from '@/views/PublicSecret.vue'

function mountView() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(PublicSecret, {
    global: { plugins: [i18n], stubs: { BrandLogo: true, BrandMark: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.peekSecret.mockReset()
  api.revealPublicSecret.mockReset()
  route.hash = '#tok-123'
  api.peekSecret.mockResolvedValue({
    data: {
      requires_passphrase: false,
      label: 'VPN',
      sender_name: 'Ann',
      expires_at: null,
      views_left: 1,
      locked_until: null,
      attempts_left: null,
    },
  })
})

describe('PublicSecret', () => {
  it('peeks with the fragment token and reveals nothing on load', async () => {
    const w = mountView()
    await flushPromises()
    expect(api.peekSecret).toHaveBeenCalledWith('tok-123')
    expect(api.revealPublicSecret).not.toHaveBeenCalled()
    expect(w.text()).toContain('VPN')
    expect(w.find('[data-testid="last-view-warning"]').exists()).toBe(true)
  })

  it('reveals on click', async () => {
    api.revealPublicSecret.mockResolvedValue({
      data: { content: 'hunter2', views_left: 0, ended: true },
    })
    const w = mountView()
    await flushPromises()
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(api.revealPublicSecret).toHaveBeenCalledWith('tok-123', null)
    expect(w.find('[data-testid="secret-content"]').text()).toBe('hunter2')
  })

  it('a link without its fragment is reported, not sent anywhere', async () => {
    route.hash = ''
    const w = mountView()
    await flushPromises()
    expect(api.peekSecret).not.toHaveBeenCalled()
    expect(w.find('[data-testid="public-secret-error"]').text()).toContain(
      en.public_secret.incomplete,
    )
  })
})
