import api from './client'
import axios from 'axios'

export interface WebAuthnCredentialItem {
  id: number
  name: string
  transports: string[]
  created_at: string
  last_used_at: string | null
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
  return api.post<{ options: Record<string, unknown> }>(
    '/account/webauthn/register/begin',
    payload,
  )
}

export function registerComplete(name: string, credential: unknown) {
  return api.post<WebAuthnCredentialItem>(
    '/account/webauthn/register/complete',
    { name, credential },
  )
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
