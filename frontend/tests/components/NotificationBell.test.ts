/* The bell loads the notifications and opens their live stream as soon as
 * someone is signed in. Both are closed to a person who must still enrol in
 * two-factor (403 TWOFA_SETUP_REQUIRED), so on the enrolment page - the only
 * page they can reach - the bell asked anyway, was refused, and kept asking
 * (found by the forced-enrolment use case, 2026-11-21). */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ listNotifications: vi.fn(), getStreamToken: vi.fn() }))
vi.mock('@/api/notifications', () => ({
  listNotifications: (p: unknown) => api.listNotifications(p),
  getStreamToken: () => api.getStreamToken(),
  deleteNotification: vi.fn(),
  deleteAllNotifications: vi.fn(),
}))
const sse = vi.hoisted(() => ({ start: vi.fn(), stop: vi.fn() }))
vi.mock('@/composables/useSSE', () => ({
  useSSE: () => ({ start: sse.start, stop: sse.stop, connected: ref(false), givenUp: ref(false) }),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

import NotificationBell from '@/components/NotificationBell.vue'
import { useAuthStore } from '@/stores/auth'
import type { MeResponse } from '@/types/api'

function mountBell(user: Partial<MeResponse>) {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.user = { id: 1, role: 'client', ...user } as MeResponse
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return { w: mount(NotificationBell, { global: { plugins: [i18n] } }), auth }
}

beforeEach(() => {
  api.listNotifications.mockReset().mockResolvedValue({ data: { items: [], unread_count: 0 } })
  api.getStreamToken.mockReset()
  sse.start.mockReset()
  sse.stop.mockReset()
})

describe('NotificationBell', () => {
  it('loads and listens for someone signed in', async () => {
    mountBell({ requires_2fa: false })
    await flushPromises()
    expect(api.listNotifications).toHaveBeenCalledTimes(1)
    expect(sse.start).toHaveBeenCalledTimes(1)
  })

  it('asks for nothing while the person must still enrol in two-factor, and starts once they have', async () => {
    const { auth } = mountBell({ requires_2fa: true })
    await flushPromises()
    expect(api.listNotifications).not.toHaveBeenCalled()
    expect(sse.start).not.toHaveBeenCalled()

    auth.user = { ...auth.user, requires_2fa: false } as MeResponse
    await flushPromises()
    expect(api.listNotifications).toHaveBeenCalledTimes(1)
    expect(sse.start).toHaveBeenCalledTimes(1)
  })
})
