/* Axios client with promise-coalesced refresh-on-401.
 *
 * - Access token lives only in memory (Pinia store), set on every successful
 *   /api/auth/login or /api/auth/refresh response. Never persisted.
 * - Refresh cookie is httpOnly + Secure + scoped to /api/auth, so it travels
 *   with /api/auth/refresh automatically and nowhere else.
 * - On a 401 response from any non-refresh endpoint, we attempt one refresh.
 *   Concurrent 401s share a single refresh promise so we don't hammer the
 *   server with five refreshes if five requests fail at once.
 * - On refresh failure, the auth store is cleared and the router pushed to
 *   /login (preserving the original target via `?redirect=...`).
 */

import axios, { type AxiosError, type AxiosRequestConfig } from 'axios'

import type { ApiErrorEnvelope, RefreshResponse } from '@/types/api'

declare module 'axios' {
  // Per-request opt-out: when true, a 401 that cannot be refreshed away does
  // NOT trigger the global onAuthLost() redirect.
  //
  // The comment here used to say the app-bootstrap session probe passes it.
  // Nothing has ever passed it: `bootstrap()` calls `refreshOnce()` FIRST and
  // only calls getMe() if that succeeded, so the probe never reaches this
  // branch and the flag was a documented escape hatch that did nothing (audit
  // 2026-07-30, fe-auth-9). It stays declared - it is the correct mechanism if
  // a caller ever does need a 401 not to navigate - but the comment now says
  // what is true, so nobody relies on protection that is not there.
  interface AxiosRequestConfig {
    _skipAuthLost?: boolean
  }
}

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  withCredentials: true,
  // Serialize array query params as repeated keys (`?state=a&state=b`)
  // instead of axios's default bracket form (`?state[]=a`). FastAPI's
  // `Query(default_factory=list)` only matches the repeated form;
  // the bracket form silently disappears, leaving the filter unapplied.
  paramsSerializer: { indexes: null },
})

// Set by the auth store on login + refresh; cleared on logout.
let accessToken: string | null = null

export function setAccessToken(token: string | null) {
  accessToken = token
}

export function getAccessToken(): string | null {
  return accessToken
}

api.interceptors.request.use((config) => {
  if (accessToken && !config.headers?.has?.('Authorization')) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${accessToken}`
  }
  return config
})

/* --- promise-coalesced refresh ------------------------------------------- */

let pendingRefresh: Promise<boolean> | null = null

export async function refreshOnce(): Promise<boolean> {
  if (pendingRefresh) return pendingRefresh
  pendingRefresh = (async () => {
    try {
      const resp = await axios.post<RefreshResponse>('/api/auth/refresh', null, {
        withCredentials: true,
      })
      setAccessToken(resp.data.access_token)
      return true
    } catch {
      setAccessToken(null)
      return false
    } finally {
      // Clear only after the awaited request settles to avoid race.
      queueMicrotask(() => {
        pendingRefresh = null
      })
    }
  })()
  return pendingRefresh
}

let onAuthLost: (() => void) | null = null

export function setOnAuthLost(fn: () => void) {
  onAuthLost = fn
}

api.interceptors.response.use(
  (r) => r,
  async (error: AxiosError) => {
    const original = error.config as AxiosRequestConfig & { _retry?: boolean }
    const status = error.response?.status
    const url = original?.url ?? ''

    const isAuthCall =
      url.includes('/auth/refresh') ||
      url.includes('/auth/login') ||
      url.includes('/auth/logout') ||
      url.includes('/auth/register-from-invite') ||
      url.includes('/auth/forgot-password') ||
      url.includes('/auth/reset-password') ||
      url.includes('/auth/verify-email') ||
      // These four answer 401 INVALID_CREDENTIALS for a WRONG SUBMITTED
      // PASSWORD from an already-signed-in caller, not for an expired session.
      // Replaying is worse than useless: the refresh always succeeds, so the
      // request goes again with the same wrong password and every visible
      // attempt spends the per-IP budget twice. change-password and
      // change-email share one 3-per-15-min bucket, so the user's SECOND typo
      // surfaces as 429 RATE_LIMITED rather than "current password is
      // incorrect". This is the hazard step_up.py answers 403 to avoid.
      //
      // Listed individually, NOT as a blanket `/account/` prefix: the rest of
      // that namespace (2fa/status, oidc/links, profile PATCHes) 401s for an
      // expired session, which is precisely what should be refreshed and
      // replayed. Excluding all of it would sign people out instead.
      url.includes('/account/change-password') ||
      url.includes('/account/email') ||
      url.includes('/account/2fa/disable') ||
      url.includes('/account/2fa/recovery-codes/regenerate')

    if (status === 401 && !original._retry && !isAuthCall) {
      original._retry = true
      const refreshed = await refreshOnce()
      if (refreshed) {
        // Drop the stale Authorization header so the request interceptor
        // injects the freshly-refreshed token; otherwise the retry replays the
        // now-expired token (the interceptor skips headers that already carry
        // an Authorization) and 401s again.
        ;(original.headers as { delete?(name: string): void } | undefined)?.delete?.(
          'Authorization',
        )
        return api(original)
      }
      // The bootstrap session-probe opts out: a failed probe just means
      // "anonymous", it must not force a navigation to /login.
      if (!original._skipAuthLost) onAuthLost?.()
    }

    return Promise.reject(error)
  },
)

/* Type-narrowing helper for components / pages handling errors. */
export function asEnvelope(e: unknown): ApiErrorEnvelope | null {
  if (axios.isAxiosError(e)) {
    const data = e.response?.data
    if (
      data &&
      typeof data === 'object' &&
      'error' in data &&
      'code' in data &&
      typeof (data as ApiErrorEnvelope).code === 'string'
    ) {
      return data as ApiErrorEnvelope
    }
  }
  return null
}

export default api
