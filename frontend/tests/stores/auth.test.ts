/* Pinia auth store unit tests - exercises the public methods with mocked
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
// running backend in unit tests - stub both so bootstrap reaches getMe()
// instead of failing on a real network call. Keep the real `@/api/client`
// otherwise (axios instance, interceptors) so the rest of the graph loads.
vi.mock('@/api/client', async (importActual) => ({
  ...(await importActual<typeof import('@/api/client')>()),
  refreshSession: vi.fn(async () => 'ok'),
  setAccessToken: vi.fn(),
  setOnAuthLost: vi.fn(),
}))
vi.mock('@/api/setup', () => ({
  getSetupStatus: vi.fn(async () => ({ data: { required: false } })),
}))
// The passkey ceremony itself needs a real authenticator; stub the two
// browser-facing pieces and exercise what the store does with the answer.
vi.mock('@/api/webauthn')
vi.mock('@/composables/useWebAuthn', () => ({
  isWebAuthnSupported: () => true,
  performAuthentication: vi.fn(async () => ({ id: 'cred', rawId: 'cred' })),
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
  admin_nav_collapse_mode: null,
  admin_nav_open_categories: null,
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

  it('an unreachable backend at cold boot is not cached as the answer', async () => {
    // bootstrap() memoises its promise and runs once per page life, so caching
    // a container-restart blip would leave the tab anonymous until a manual
    // reload. `expired` IS cached - that is a real verdict.
    const { refreshSession } = await import('@/api/client')
    vi.mocked(refreshSession).mockResolvedValueOnce('unavailable')
    const auth = useAuthStore()
    await auth.bootstrap()
    expect(auth.user).toBe(null)

    // Backend is back: the next bootstrap must actually re-ask - but not before
    // the cooldown, which is what stops `beforeEach` re-probing on every click.
    vi.mocked(refreshSession).mockResolvedValueOnce('ok')
    vi.mocked(accountApi.getMe).mockResolvedValueOnce({ data: fakeMe } as never)
    await auth.bootstrap()
    expect(auth.user, 'a click during the cooldown must not re-probe').toBe(null)

    vi.useFakeTimers()
    try {
      vi.setSystemTime(Date.now() + 5_000)
      await auth.bootstrap()
    } finally {
      vi.useRealTimers()
    }
    expect(auth.user).toEqual(fakeMe)
    expect(auth.isAuthenticated).toBe(true)
  })

  it('bootstrap never signs out an already-signed-in user', async () => {
    // Dropping the memo means this can run again, on any navigation. Its else
    // branch nulls `user`, which was only safe while it ran once at cold start.
    // A later transient blip would otherwise end a live session and the router
    // guard would redirect to /login - the restart-logs-you-out bug re-entering
    // through the store. Signing out is the interceptor's job, on a verdict.
    // The sequence has to be the real one, or the memo hides the bug: a cold
    // boot that FAILED (dropping the memo), then a sign-in, then a later
    // navigation re-entering bootstrap. Asserting on a run whose memo is still
    // set proves nothing - it never re-runs.
    const { refreshSession } = await import('@/api/client')
    const auth = useAuthStore()

    vi.mocked(refreshSession).mockResolvedValueOnce('unavailable')
    await auth.bootstrap() // cold boot during a restart -> memo dropped
    expect(auth.user).toBe(null)

    vi.mocked(authApi.login).mockResolvedValueOnce({
      data: { access_token: 'tok', expires_in_seconds: 900 },
    } as never)
    vi.mocked(accountApi.getMe).mockResolvedValueOnce({ data: fakeMe } as never)
    await auth.login('a@example.com', 'password!')
    expect(auth.isAuthenticated).toBe(true)

    // The backend blips again while they are signed in. bootstrap() runs on
    // EVERY navigation, and its else branch nulls `user` - which would end a
    // live session and bounce them to /login via the router guard.
    vi.mocked(refreshSession).mockResolvedValue('unavailable')
    vi.useFakeTimers()
    try {
      vi.setSystemTime(Date.now() + 10_000) // past the re-probe cooldown
      await auth.bootstrap()
      await auth.bootstrap()
    } finally {
      vi.useRealTimers()
      vi.mocked(refreshSession).mockResolvedValue('ok')
    }
    expect(auth.user, 'a live session must survive a re-probe').toEqual(fakeMe)
    expect(auth.isAuthenticated).toBe(true)
  })

  it('a real verdict at cold boot IS cached', async () => {
    const { refreshSession } = await import('@/api/client')
    // `Once`, so the implementation does not leak into later tests -
    // vi.clearAllMocks() resets call history but not a persistent impl.
    vi.mocked(refreshSession).mockResolvedValueOnce('expired')
    const auth = useAuthStore()
    await auth.bootstrap()
    await auth.bootstrap()
    expect(vi.mocked(refreshSession)).toHaveBeenCalledTimes(1)
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

  it('a /me blip after a successful refresh is not cached as anonymous', async () => {
    // The refresh minted a token, then /me hit the proxy's 503 during the
    // in-app updater's restart. That is the same "no answer" as an
    // `unavailable` refresh: memoising it stranded a valid session until a
    // manual reload, with the router bouncing every navigation to /login.
    vi.mocked(accountApi.getMe).mockRejectedValueOnce({ response: { status: 503 } })
    const auth = useAuthStore()
    await auth.bootstrap()
    expect(auth.user).toBe(null)

    vi.mocked(accountApi.getMe).mockResolvedValueOnce({ data: fakeMe } as never)
    vi.useFakeTimers()
    try {
      vi.setSystemTime(Date.now() + 5_000) // past the re-probe cooldown
      await auth.bootstrap()
    } finally {
      vi.useRealTimers()
    }
    expect(auth.user).toEqual(fakeMe)
    expect(auth.isAuthenticated).toBe(true)
  })

  it('a /me verdict after a successful refresh IS cached', async () => {
    vi.mocked(accountApi.getMe).mockRejectedValueOnce({ response: { status: 401 } })
    const auth = useAuthStore()
    await auth.bootstrap()
    vi.useFakeTimers()
    try {
      vi.setSystemTime(Date.now() + 5_000)
      await auth.bootstrap()
    } finally {
      vi.useRealTimers()
    }
    expect(vi.mocked(accountApi.getMe)).toHaveBeenCalledTimes(1)
    expect(auth.user).toBe(null)
  })

  it('loginWithPasskey() hands a pending-2FA answer back instead of adopting an empty token', async () => {
    // The backend answers `pending_2fa_token` (no access_token at all) when
    // the assertion carried no user verification on a TOTP-enrolled account.
    // This used to be fed to setAccessToken as `undefined` and then /me 401'd -
    // for exactly the users the "Use passkey" button is shown to.
    const webauthnApi = await import('@/api/webauthn')
    const { setAccessToken } = await import('@/api/client')
    vi.mocked(webauthnApi.authBegin).mockResolvedValueOnce({
      data: { session: 'sess', options: { challenge: 'c', rpId: 'files.example' } },
    } as never)
    vi.mocked(webauthnApi.authComplete).mockResolvedValueOnce({
      data: { pending_2fa_token: 'pend-123' },
    } as never)
    const auth = useAuthStore()

    const result = await auth.loginWithPasskey('a@example.com', 'pw')

    expect(result).toEqual({ status: 'pending_2fa', pendingToken: 'pend-123' })
    expect(setAccessToken).not.toHaveBeenCalled()
    expect(accountApi.getMe).not.toHaveBeenCalled()
    expect(auth.isAuthenticated).toBe(false)
  })

  it('loginWithPasskey() adopts a real session', async () => {
    const webauthnApi = await import('@/api/webauthn')
    const { setAccessToken } = await import('@/api/client')
    vi.mocked(webauthnApi.authBegin).mockResolvedValueOnce({
      data: { session: 'sess', options: { challenge: 'c', rpId: 'files.example' } },
    } as never)
    vi.mocked(webauthnApi.authComplete).mockResolvedValueOnce({
      data: { access_token: 'tok', expires_in_seconds: 900 },
    } as never)
    vi.mocked(accountApi.getMe).mockResolvedValueOnce({ data: fakeMe } as never)
    const auth = useAuthStore()

    const result = await auth.loginWithPasskey('a@example.com', 'pw')

    expect(result).toEqual({ status: 'ok', me: fakeMe })
    expect(setAccessToken).toHaveBeenCalledWith('tok')
    expect(auth.user).toEqual(fakeMe)
  })

  it('loginWithPasskey() refuses a 200 that carries neither shape', async () => {
    // A captive portal / SPA-fallback misconfiguration answering 200 text/html.
    const webauthnApi = await import('@/api/webauthn')
    vi.mocked(webauthnApi.authBegin).mockResolvedValueOnce({
      data: { session: 'sess', options: { challenge: 'c', rpId: 'files.example' } },
    } as never)
    vi.mocked(webauthnApi.authComplete).mockResolvedValueOnce({ data: '<html>' } as never)
    const auth = useAuthStore()
    await expect(auth.loginWithPasskey('a@example.com', 'pw')).rejects.toThrow()
    expect(auth.isAuthenticated).toBe(false)
  })
})
