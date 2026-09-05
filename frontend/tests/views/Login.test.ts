/* The login page reads three things off its URL: `?redirect=`, `?oidc_error=`
 * and - since the second-factor interstitial sends it - `?expired=1`. The last
 * one was sent for as long as the interstitial existed and never read, so a
 * user whose five-minute pending token lapsed landed on a bare form with no
 * explanation. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const replace = vi.fn(async () => {})
const push = vi.fn(async () => {})
let query: Record<string, string> = {}
vi.mock('vue-router', () => ({
  useRoute: () => ({ query, path: '/login' }),
  useRouter: () => ({ replace, push }),
}))
vi.mock('@/api/client', async (importActual) => ({
  ...(await importActual<typeof import('@/api/client')>()),
  refreshSession: vi.fn(async () => 'unavailable'),
  setAccessToken: vi.fn(),
  setOnAuthLost: vi.fn(),
}))

import Login from '@/views/Login.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(Login, { global: { plugins: [i18n], stubs: { RouterLink: true } } })
}

describe('Login', () => {
  beforeEach(() => {
    replace.mockClear()
    push.mockClear()
    query = {}
  })

  it('explains an expired second-factor step and cleans the URL', async () => {
    query = { expired: '1' }
    const w = makeWrapper()
    await flushPromises()

    expect(w.find('[role="alert"]').text()).toContain('second-factor step expired')
    expect(replace).toHaveBeenCalledWith({ path: '/login', query: {} })
  })

  it('still translates a failed SSO callback', async () => {
    query = { oidc_error: 'OIDC_NO_ACCOUNT' }
    const w = makeWrapper()
    await flushPromises()

    expect(w.find('[role="alert"]').text()).toBe(en.errors.OIDC_NO_ACCOUNT)
    expect(replace).toHaveBeenCalledWith({ path: '/login', query: {} })
  })

  it('shows no alert on a plain visit', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(replace).not.toHaveBeenCalled()
  })
})
