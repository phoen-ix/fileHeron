/* Pinia auth store unit tests — exercises the public methods with mocked
 * api modules so we don't need a running backend. */

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as authApi from '@/api/auth'
import * as accountApi from '@/api/account'
import { useAuthStore } from '@/stores/auth'

vi.mock('@/api/auth')
vi.mock('@/api/account')

// bootstrap() refreshes FIRST (cookie-based) and only loads /me when that
// succeeds, and separately probes the first-admin wizard. Neither has a
// running backend in unit tests — stub both so bootstrap reaches getMe()
// instead of failing on a real network call. Keep the real `@/api/client`
// otherwise (axios instance, interceptors) so the rest of the graph loads.
vi.mock('@/api/client', async (importActual) => ({
  ...(await importActual<typeof import('@/api/client')>()),
  refreshOnce: vi.fn(async () => true),
  setAccessToken: vi.fn(),
  setOnAuthLost: vi.fn(),
}))
vi.mock('@/api/setup', () => ({
  getSetupStatus: vi.fn(async () => ({ data: { required: false } })),
}))

const fakeMe = {
  id: 1,
  email_hint: 'a***@example.com',
  display_name: 'Alice',
  role: 'employee' as const,
  locale: 'en' as const,
  email_verified: true,
  is_disabled: false,
  created_at: '2026-05-02T00:00:00',
  last_login_at: null,
  quota_bytes: null,
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useAuthStore', () => {
  it('starts unauthenticated and bootstrapping=true', () => {
    const auth = useAuthStore()
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.bootstrapping).toBe(true)
    expect(auth.user).toBe(null)
  })

  it('bootstrap() succeeds → loads user, sets bootstrapping=false', async () => {
    vi.mocked(accountApi.getMe).mockResolvedValueOnce({ data: fakeMe } as never)
    const auth = useAuthStore()
    await auth.bootstrap()
    expect(auth.bootstrapping).toBe(false)
    expect(auth.user).toEqual(fakeMe)
    expect(auth.isAuthenticated).toBe(true)
  })

  it('bootstrap() fails → user stays null', async () => {
    vi.mocked(accountApi.getMe).mockRejectedValueOnce(new Error('401'))
    const auth = useAuthStore()
    await auth.bootstrap()
    expect(auth.bootstrapping).toBe(false)
    expect(auth.user).toBe(null)
  })

  it('login() calls authApi.login then loads /me', async () => {
    vi.mocked(authApi.login).mockResolvedValueOnce({
      data: { access_token: 'tok', expires_in_seconds: 900 },
    } as never)
    vi.mocked(accountApi.getMe).mockResolvedValueOnce({ data: fakeMe } as never)
    const auth = useAuthStore()
    await auth.login('a@example.com', 'password!')
    expect(authApi.login).toHaveBeenCalledWith({
      email: 'a@example.com',
      password: 'password!',
      totp_code: undefined,
    })
    expect(auth.user).toEqual(fakeMe)
  })

  it('login() with totp_code passes it through', async () => {
    vi.mocked(authApi.login).mockResolvedValueOnce({
      data: { access_token: 'tok', expires_in_seconds: 900 },
    } as never)
    vi.mocked(accountApi.getMe).mockResolvedValueOnce({ data: fakeMe } as never)
    const auth = useAuthStore()
    await auth.login('a@example.com', 'password!', '123456')
    expect(authApi.login).toHaveBeenCalledWith({
      email: 'a@example.com',
      password: 'password!',
      totp_code: '123456',
    })
  })

  it('loginWithRecovery() uses the recovery endpoint', async () => {
    vi.mocked(authApi.loginWithRecovery).mockResolvedValueOnce({
      data: { access_token: 'tok', expires_in_seconds: 900 },
    } as never)
    vi.mocked(accountApi.getMe).mockResolvedValueOnce({ data: fakeMe } as never)
    const auth = useAuthStore()
    await auth.loginWithRecovery('a@example.com', 'pw', 'AAAA-BBBB')
    expect(authApi.loginWithRecovery).toHaveBeenCalledWith({
      email: 'a@example.com',
      password: 'pw',
      recovery_code: 'AAAA-BBBB',
    })
  })

  it('logout() clears state even if the API call fails', async () => {
    vi.mocked(authApi.logout).mockRejectedValueOnce(new Error('network'))
    vi.mocked(accountApi.getMe).mockResolvedValueOnce({ data: fakeMe } as never)
    const auth = useAuthStore()
    await auth.bootstrap()
    expect(auth.isAuthenticated).toBe(true)
    await auth.logout()
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.user).toBe(null)
  })

  it('refreshMe() reloads the user record', async () => {
    vi.mocked(accountApi.getMe).mockResolvedValueOnce({ data: fakeMe } as never)
    const auth = useAuthStore()
    await auth.bootstrap()

    vi.mocked(accountApi.getMe).mockResolvedValueOnce({
      data: { ...fakeMe, display_name: 'Alice Smith' },
    } as never)
    const me = await auth.refreshMe()
    expect(me.display_name).toBe('Alice Smith')
    expect(auth.user?.display_name).toBe('Alice Smith')
  })
})
