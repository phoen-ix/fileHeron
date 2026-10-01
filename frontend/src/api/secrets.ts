import axios from 'axios'

import api from './client'
import type {
  AdminSecretListResponse,
  CreateSecretRequest,
  PublicSecretPeekResponse,
  RevealSecretResponse,
  SecretLinkResponse,
  SecretLinksResponse,
  SecretListResponse,
  SecretPolicyResponse,
  SecretResponse,
  SecretState,
  UpdateSecretPolicyRequest,
} from '@/types/api'

/* Secrets (v2.24.0). Only the two reveal calls ever return a secret's
 * content, and only to a recipient - nothing else here does. */

export function createSecret(payload: CreateSecretRequest) {
  return api.post<SecretResponse>('/secrets', payload)
}

export function listSecrets(params: {
  box: 'received' | 'sent'
  state?: SecretState[]
  q?: string
  page?: number
  page_size?: number
}) {
  return api.get<SecretListResponse>('/secrets', { params })
}

export function getSecret(id: string) {
  return api.get<SecretResponse>(`/secrets/${id}`)
}

export function revealSecret(id: string, passphrase: string | null) {
  return api.post<RevealSecretResponse>(`/secrets/${id}/reveal`, { passphrase })
}

export function burnSecret(id: string) {
  return api.post<SecretResponse>(`/secrets/${id}/burn`)
}

/** The sender's own links, readable again. Every call is audited. */
export function getSecretLinks(id: string) {
  return api.get<SecretLinksResponse>(`/secrets/${id}/links`)
}

/** Create the copyable link, or replace it: the old one stops working. */
export function replaceSecretLink(id: string) {
  return api.post<SecretLinkResponse>(`/secrets/${id}/link`)
}

export function removeSecretLink(id: string) {
  return api.delete(`/secrets/${id}/link`)
}

/* Anonymous: the page is /s#<token>. The token stays in the fragment - never
 * in a path or query string, where every proxy and access log would keep it -
 * and travels to the API in a POST body. Both calls are POSTs on purpose: a
 * mail gateway that prefetches the link costs nothing, only an explicit
 * reveal uses a view. */

const publicClient = axios.create({ baseURL: '/' })

export function peekSecret(token: string) {
  return publicClient.post<PublicSecretPeekResponse>('/api/public/secrets/peek', { token })
}

export function revealPublicSecret(token: string, passphrase: string | null) {
  return publicClient.post<RevealSecretResponse>('/api/public/secrets/reveal', {
    token,
    passphrase,
  })
}

/* Admin: metadata only - there is no admin route to a secret's content. */

export function listAllSecrets(params: {
  state?: SecretState[]
  q?: string
  page?: number
  page_size?: number
}) {
  return api.get<AdminSecretListResponse>('/admin/secrets', { params })
}

export function getSecretPolicy() {
  return api.get<SecretPolicyResponse>('/admin/settings/secrets')
}

export function updateSecretPolicy(payload: UpdateSecretPolicyRequest) {
  return api.put<SecretPolicyResponse>('/admin/settings/secrets', payload)
}
