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

export function registerBegin() {
  return api.post<{ options: Record<string, unknown> }>(
    '/account/webauthn/register/begin',
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

export function authComplete(session: string, credential: unknown) {
  return anonClient.post<{ access_token: string; expires_in_seconds: number }>(
    '/auth/webauthn/complete',
    { session, credential },
    { withCredentials: true },
  )
}
