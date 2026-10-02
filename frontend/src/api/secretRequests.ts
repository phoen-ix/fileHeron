import axios from 'axios'

import api from './client'
import type {
  AdminSecretRequestListResponse,
  AnswerSecretRequestResponse,
  CreateSecretRequestRequest,
  PublicSecretRequestPeekResponse,
  SecretRequestLinksResponse,
  SecretRequestListResponse,
  SecretRequestResponse,
  SecretRequestState,
} from '@/types/api'

/* Secret requests (v2.24.0): asking someone for a secret. The answer travels
 * IN through the two answer calls and comes out only as an ordinary secret,
 * revealed by the requester through api/secrets.ts. */

export function createSecretRequest(payload: CreateSecretRequestRequest) {
  return api.post<SecretRequestResponse>('/secret-requests', payload)
}

export function listSecretRequests(params: {
  box: 'mine' | 'asked'
  state?: SecretRequestState[]
  q?: string
  page?: number
  page_size?: number
}) {
  return api.get<SecretRequestListResponse>('/secret-requests', { params })
}

export function getSecretRequest(id: string) {
  return api.get<SecretRequestResponse>(`/secret-requests/${id}`)
}

export function answerSecretRequest(id: string, content: string, passphrase: string | null) {
  return api.post<AnswerSecretRequestResponse>(`/secret-requests/${id}/answer`, {
    content,
    passphrase,
  })
}

export function cancelSecretRequest(id: string) {
  return api.post<SecretRequestResponse>(`/secret-requests/${id}/cancel`)
}

/** The requester's own answer links, readable again. Every call is audited. */
export function getSecretRequestLinks(id: string) {
  return api.get<SecretRequestLinksResponse>(`/secret-requests/${id}/links`)
}

/* Anonymous: the page is /r#<token>. The token stays in the fragment and
 * travels in a POST body - never a path or query string, where proxies and
 * access logs keep it. */

const publicClient = axios.create({ baseURL: '/' })

export function peekSecretRequest(token: string) {
  return publicClient.post<PublicSecretRequestPeekResponse>('/api/public/secret-requests/peek', {
    token,
  })
}

export function answerPublicSecretRequest(
  token: string,
  content: string,
  passphrase: string | null,
) {
  return publicClient.post<AnswerSecretRequestResponse>('/api/public/secret-requests/answer', {
    token,
    content,
    passphrase,
  })
}

/* Admin: metadata only, plus Cancel. */

export function listAllSecretRequests(params: {
  state?: SecretRequestState[]
  q?: string
  page?: number
  page_size?: number
}) {
  return api.get<AdminSecretRequestListResponse>('/admin/secret-requests', { params })
}

export function adminCancelSecretRequest(id: string) {
  return api.post<SecretRequestResponse>(`/admin/secret-requests/${id}/cancel`)
}
