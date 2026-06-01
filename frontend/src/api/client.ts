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
  // Per-request opt-out: when true, a 401 that can't be refreshed away does
  // NOT trigger the global onAuthLost() redirect. Used by the app-bootstrap
  // session probe (getMe) so an anonymous visitor on a PUBLIC page isn't
  // bounced to /login before the router guard can honor meta.public.
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

async function refreshOnce(): Promise<boolean> {
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
      url.includes('/auth/verify-email')

    if (status === 401 && !original._retry && !isAuthCall) {
      original._retry = true
      const refreshed = await refreshOnce()
      if (refreshed) {
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
