/* Single source of truth for auth state. Access token lives only here (in
 * memory) — never persisted. Refresh cookie is httpOnly + scoped, set by
 * the server. */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import * as authApi from '@/api/auth'
import { getMe } from '@/api/account'
import { setAccessToken, setOnAuthLost } from '@/api/client'
import * as webauthnApi from '@/api/webauthn'
import { performAuthentication } from '@/composables/useWebAuthn'
import type { Locale, MeResponse, UserRole } from '@/types/api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<MeResponse | null>(null)
  const isAuthenticated = computed(() => user.value !== null)
  const role = computed<UserRole | null>(() => user.value?.role ?? null)
  const locale = computed<Locale>(() => user.value?.locale ?? 'en')

  /* True while we're attempting a silent refresh on app boot. The router
   * uses this to defer guard decisions until we know whether a session
   * exists. */
  const bootstrapping = ref(true)

  let onAuthLostCallback: (() => void) | null = null

  /** Attempt a silent refresh + load /me on first paint. */
  async function bootstrap() {
    bootstrapping.value = true
    try {
      // GET /me — succeeds if access token is still valid; otherwise the
      // axios interceptor will try /auth/refresh and retry once.
      const resp = await getMe()
      user.value = resp.data
    } catch {
      user.value = null
    } finally {
      bootstrapping.value = false
    }
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

  async function logout() {
    try {
      await authApi.logout()
    } catch {
      /* network error — proceed to clear local state regardless */
    }
    setAccessToken(null)
    user.value = null
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
