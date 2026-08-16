/* Single source of truth for auth state. Access token lives only here (in
 * memory) - never persisted. Refresh cookie is httpOnly + scoped, set by
 * the server. */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import * as authApi from '@/api/auth'
import { getMe } from '@/api/account'
import { refreshSession, setAccessToken, setOnAuthLost } from '@/api/client'
import { setLocale, type SupportedLocale } from '@/i18n'
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

  /* True while we're attempting a silent refresh on app boot. Nothing reads it
   * any more - the router awaits bootstrap()'s promise directly - but it is
   * cheap and still describes the store's state honestly. */
  const bootstrapping = ref(true)
  /* Cache the in-flight (or completed) bootstrap promise. `router.beforeEach`
   * awaits it on EVERY navigation and main.ts gates app.mount() on it, so the
   * cache is what stops the whole probe re-running per click. It is dropped in
   * exactly one case - see the `unreachable` handling below. */
  let bootstrapPromise: Promise<void> | null = null
  /* When the memo is dropped, `beforeEach` would otherwise re-probe on every
   * single click - a refresh (lock acquisition + POST) plus a setup probe, with
   * the router awaiting all of it. doRefresh's own comment forbids turning the
   * refresh into a sleep-and-retry loop; a per-navigation loop is the same thing
   * driven by the user. Re-probing is still what recovers the session, so bound
   * it rather than remove it. */
  let lastUnreachableAt = 0
  const REPROBE_COOLDOWN_MS = 3_000

  let onAuthLostCallback: (() => void) | null = null

  /** Attempt a silent refresh + load /me on first paint. Idempotent - the
   *  first caller kicks off the request, all subsequent callers receive the
   *  same promise. */
  function bootstrap(): Promise<void> {
    if (bootstrapPromise) return bootstrapPromise
    // Dropping the memo on `unavailable` means this can now run more than once,
    // which broke an invariant it used to rely on: that it only ever ran at cold
    // start, before anyone could be signed in. Once a session exists, re-probing
    // must never be able to END it - a later transient blip would otherwise null
    // `user` and the router guard would redirect to /login, which is precisely
    // the restart-logs-you-out bug this all exists to prevent, re-entering
    // through the store instead of the interceptor. Signing out is the
    // interceptor's job, on a real verdict; bootstrap only ever ADOPTS a session.
    if (user.value !== null) return Promise.resolve()
    if (lastUnreachableAt && Date.now() - lastUnreachableAt < REPROBE_COOLDOWN_MS) {
      return Promise.resolve()
    }
    bootstrapping.value = true
    let unreachable = false
    bootstrapPromise = (async () => {
      try {
        // Cold load has no in-memory access token, so refresh FIRST (via the
        // httpOnly cookie) instead of letting /me 401 then retry - that saves a
        // request and avoids a visible 401 in devtools. `refreshSession` swallows
        // failure (it never triggers onAuthLost), so an anonymous visitor on a
        // public /d/:token page is not bounced to /login.
        const outcome = await refreshSession()
        if (outcome === 'ok') {
          const resp = await getMe()
          user.value = resp.data
        } else {
          // `unavailable` still renders as anonymous - a cold load against an
          // unreachable backend has nothing better to offer, and the router
          // guard sends a protected route to /login from here. But it must not
          // be CACHED as the answer: bootstrap runs once per page life, so
          // memoising a container-restart blip would leave the tab anonymous
          // until a manual reload. Dropping the memo lets the next navigation
          // ask again.
          unreachable = outcome === 'unavailable'
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
      if (unreachable) {
        lastUnreachableAt = Date.now()
        bootstrapPromise = null
      }
    })()
    return bootstrapPromise
  }

  /** Apply the signed-in user's language.
   *
   * The locale was applied only at app bootstrap (main.ts / App.vue), so an
   * in-tab logout and sign-in as a different user left the FIRST user's
   * language in place until a full page reload - and a German user signing in
   * after an English one read an English UI while their profile said German
   * (audit 2026-07-30, fe-auth-8). Every login path funnels through here. */
  function applyUserLocale(me: MeResponse): MeResponse {
    if (me.locale) setLocale(me.locale as SupportedLocale)
    return me
  }

  async function login(email: string, password: string, totpCode?: string) {
    const resp = await authApi.login({ email, password, totp_code: totpCode })
    setAccessToken(resp.data.access_token)
    const me = await getMe()
    user.value = me.data
    return applyUserLocale(me.data)
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
    return applyUserLocale(me.data)
  }

  /** Finish a login whose FIRST factor was SSO or a passkey.
   *
   *  Neither of those paths challenged an enrolled TOTP factor, so switching
   *  2FA on did nothing for anyone who signed in that way. The backend now
   *  hands back a short-lived pending token instead of a session, and this
   *  exchanges it for the real one. */
  async function completeSecondFactor(
    pendingToken: string,
    factor: { totpCode?: string; recoveryCode?: string },
  ) {
    const resp = await authApi.completeSecondFactor({
      pending_token: pendingToken,
      totp_code: factor.totpCode,
      recovery_code: factor.recoveryCode,
    })
    setAccessToken(resp.data.access_token)
    const me = await getMe()
    user.value = me.data
    return applyUserLocale(me.data)
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
    return applyUserLocale(me.data)
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
    return applyUserLocale(me.data)
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
      // bootstrap() -> refreshSession() and silently restored the session. On a
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
      // Same reset as logout(). Only the explicit logout cleared the bell, so
      // an EXPIRED session left the previous user's notifications on screen for
      // whoever signed in next on that browser (audit 2026-07-30, fe-auth-4).
      useNotificationsStore().reset()
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
    completeSecondFactor,
    loginWithPasskey,
    registerFromInvite,
    logout,
    refreshMe,
    registerAuthLostHandler,
  }
})
