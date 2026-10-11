import api from './client'
import axios from 'axios'

export interface WebAuthnCredentialItem {
  id: number
  name: string
  transports: string[]
  created_at: string
  last_used_at: string | null
  /** The browser said the passkey lives on the device, so it can sign in with
   *  no email or password. null: not reported (passkeys added before v2.27.0). */
  discoverable: boolean | null
}

export interface WebAuthnCredentialListResponse {
  items: WebAuthnCredentialItem[]
}

export function listCredentials() {
  return api.get<WebAuthnCredentialListResponse>('/account/webauthn')
}

export interface WebAuthnRegisterBeginRequest {
  password: string
}

/** Step-up: a UV-verified passkey can stand in for TOTP at login, so adding
 *  one costs the current password, exactly like disabling TOTP does. */
export function registerBegin(password: string) {
  const payload: WebAuthnRegisterBeginRequest = { password }
  return api.post<{ options: Record<string, unknown> }>('/account/webauthn/register/begin', payload)
}

export function registerComplete(name: string, credential: unknown) {
  return api.post<WebAuthnCredentialItem>('/account/webauthn/register/complete', {
    name,
    credential,
  })
}

export function deleteCredential(id: number) {
  return api.delete(`/account/webauthn/${id}`)
}

/* The login flow is anonymous-until-completion, so we use a bare
 * axios that doesn't attach the (non-existent) bearer token. */
const anonClient = axios.create({ baseURL: '/api' })

export function authBegin(email: string, password: string) {
  return anonClient.post<{
    session: string
    options: Record<string, unknown>
  }>('/auth/webauthn/begin', { email, password })
}

/** Two shapes, mutually exclusive: a real session, or - when the account has
 *  TOTP enrolled and the assertion carried no user verification - a pending
 *  token for the /login/2fa interstitial. The backend omits the absent keys
 *  entirely, so every field is optional here. */
export interface WebAuthnAuthCompleteResponse {
  access_token?: string
  expires_in_seconds?: number
  pending_2fa_token?: string
}

export function authComplete(session: string, credential: unknown) {
  return anonClient.post<WebAuthnAuthCompleteResponse>(
    '/auth/webauthn/complete',
    { session, credential },
    { withCredentials: true },
  )
}

export interface PasskeyBeginResponse {
  session: string
  options: Record<string, unknown>
}

export interface PasskeyLoginResponse {
  access_token: string
  expires_in_seconds: number
}

/** Passkey sign-in (v2.27.0, while an admin has it on): the options name no
 *  account, so the browser offers whichever passkeys it holds for this site. */
export function passkeySigninBegin() {
  return anonClient.post<PasskeyBeginResponse>('/auth/passkey/begin')
}

export function passkeySigninComplete(session: string, credential: unknown) {
  return anonClient.post<PasskeyLoginResponse>(
    '/auth/passkey/complete',
    { session, credential },
    { withCredentials: true },
  )
}

/** Finish a pending sign-in (after SSO) with a passkey instead of a code. */
export function pendingPasskeyBegin(pendingToken: string) {
  return anonClient.post<PasskeyBeginResponse>('/auth/2fa/passkey/begin', {
    pending_token: pendingToken,
  })
}

export function pendingPasskeyComplete(pendingToken: string, session: string, credential: unknown) {
  return anonClient.post<PasskeyLoginResponse>(
    '/auth/2fa/passkey/complete',
    { pending_token: pendingToken, session, credential },
    { withCredentials: true },
  )
}
