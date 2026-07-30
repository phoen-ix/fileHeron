/* Single source of truth for auth state. Access token lives only here (in
 * memory) - never persisted. Refresh cookie is httpOnly + scoped, set by
 * the server. */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import * as authApi from '@/api/auth'
import { getMe } from '@/api/account'
import { refreshOnce, setAccessToken, setOnAuthLost } from '@/api/client'
import { useNotificationsStore } from '@/stores/notifications'
import * as webauthnApi from '@/api/webauthn'
import { performAuthentication } from '@/composables/useWebAuthn'
import type { Locale, MeResponse, UserRole } from '@/types/api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<MeResponse | null>(null)
  const isAuthenticated = computed(() => user.value !== null)
  const role = computed<UserRole | null>(() => user.value?.role ?? null)
  const locale = computed<Locale>(() => user.value?.locale ?? 'en')

  /* True when no admin exists yet - first-time install, SPA bounces
   * every route to /setup. Polled once during bootstrap. */
  const setupRequired = ref(false)

  /* True while we're attempting a silent refresh on app boot. The router
   * uses this to defer guard decisions until we know whether a session
   * exists. */
  const bootstrapping = ref(true)
  /* Cache the in-flight (or completed) bootstrap promise. The router guard
   * awaits it on the first navigation - every subsequent guard call gets
   * the same already-resolved promise (no-op). Replaces a 30 ms setInterval
   * polling loop. */
  let bootstrapPromise: Promise<void> | null = null

  let onAuthLostCallback: (() => void) | null = null

  /** Attempt a silent refresh + load /me on first paint. Idempotent - the
   *  first caller kicks off the request, all subsequent callers receive the
   *  same promise. */
  function bootstrap(): Promise<void> {
    if (bootstrapPromise) return bootstrapPromise
    bootstrapping.value = true
    bootstrapPromise = (async () => {
      try {
        // Cold load has no in-memory access token, so refresh FIRST (via the
        // httpOnly cookie) instead of letting /me 401 then retry - that saves a
        // request and avoids a visible 401 in devtools. `refreshOnce` swallows
        // failure (returns false, never triggers onAuthLost), so an anonymous
        // visitor on a public /d/:token page is not bounced to /login.
        if (await refreshOnce()) {
          const resp = await getMe()
          user.value = resp.data
        } else {
          user.value = null
        }
      } catch {
        user.value = null
      }
      // Independently check whether the first-admin wizard is required.
      // Anonymous endpoint; fail-open (treat unreachable as "not required")
      // so dev/offline use doesn't bounce everyone to a broken /setup.
      try {
        const { getSetupStatus } = await import('@/api/setup')
        const sr = await getSetupStatus()
        setupRequired.value = sr.data.required
      } catch {
        setupRequired.value = false
      }
      bootstrapping.value = false
    })()
    return bootstrapPromise
  }

  async function login(email: string, password: string, totpCode?: string) {
    const resp = await authApi.login({ email, password, totp_code: totpCode })
    setAccessToken(resp.data.access_token)
    const me = await getMe()
    user.value = me.data
    return me.data
  }

  async function loginWithRecovery(email: string, password: string, recoveryCode: string) {
    const resp = await authApi.loginWithRecovery({
      email,
      password,
      recovery_code: recoveryCode,
    })
    setAccessToken(resp.data.access_token)
    const me = await getMe()
    user.value = me.data
    return me.data
  }

  /** Passkey-as-second-factor login. Validates email + password,
   *  hands the user a WebAuthn challenge, then completes the
   *  ceremony to mint the same JWT + refresh-cookie as the
   *  password-only path. */
  async function loginWithPasskey(email: string, password: string) {
    const begin = await webauthnApi.authBegin(email, password)
    const opts = begin.data.options as {
      challenge: string
      rpId: string
      allowCredentials?: { id: string; type: 'public-key'; transports?: string[] }[]
      userVerification?: 'required' | 'preferred' | 'discouraged'
      timeout?: number
    }
    const credential = await performAuthentication({
      challenge: opts.challenge,
      rpId: opts.rpId,
      allowCredentials: opts.allowCredentials,
      userVerification: opts.userVerification,
      timeout: opts.timeout,
    })
    const resp = await webauthnApi.authComplete(begin.data.session, credential)
    setAccessToken(resp.data.access_token)
    const me = await getMe()
    user.value = me.data
    return me.data
  }

  async function registerFromInvite(payload: {
    token: string
    password: string
    display_name: string
    locale: Locale
  }) {
    const resp = await authApi.registerFromInvite(payload)
    setAccessToken(resp.data.access_token)
    const me = await getMe()
    user.value = me.data
    return me.data
  }

  /** Returns true when the server-side session was actually revoked. */
  async function logout(): Promise<boolean> {
    let revoked = false
    try {
      await authApi.logout()
      revoked = true
    } catch {
      // The server-side revoke AND the fh_refresh deletion both ride this
      // response, and the cookie is httpOnly + path-scoped so JS cannot clear
      // it here. Swallowing the failure showed the user the login page while a
      // 7-day refresh token stayed live - and the next full page load ran
      // bootstrap() -> refreshOnce() and silently restored the session. On a
      // shared machine that is a logout that did not log out
      // (audit 2026-07-30). Local state is still cleared (below) so the tab is
      // not left authenticated, but the caller is told the truth so it can warn.
    }
    setAccessToken(null)
    user.value = null
    // Clear the previous user's cached notifications, or the next person to
    // sign in on this browser sees them (fe-auth-4).
    useNotificationsStore().reset()
    return revoked
  }

  /** Force-refresh /me (after a profile update). */
  async function refreshMe() {
    const resp = await getMe()
    user.value = resp.data
    return resp.data
  }

  /** Wired by the router on init: when refresh fails, push to /login. */
  function registerAuthLostHandler(fn: () => void) {
    onAuthLostCallback = fn
    setOnAuthLost(() => {
      setAccessToken(null)
      user.value = null
      onAuthLostCallback?.()
    })
  }

  return {
    user,
    isAuthenticated,
    role,
    locale,
    setupRequired,
    bootstrapping,
    bootstrap,
    login,
    loginWithRecovery,
    loginWithPasskey,
    registerFromInvite,
    logout,
    refreshMe,
    registerAuthLostHandler,
  }
})
