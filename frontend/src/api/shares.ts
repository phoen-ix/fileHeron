import api from './client'
import type {
  PublicLinkOnCreate,
  ShareKind,
  ShareListResponse,
  ShareRecipientsRequest,
  ShareResponse,
} from '@/types/api'

export function createShare(payload: {
  kind: ShareKind
  recipients: ShareRecipientsRequest
  expires_at: string
  subject?: string | null
  message?: string | null
  public_link?: PublicLinkOnCreate | null
}) {
  return api.post<ShareResponse>('/shares', payload)
}

export function updateShareExpiry(shareId: string, expires_at: string) {
  return api.patch<ShareResponse>(`/shares/${shareId}`, { expires_at })
}

export function expireShareNow(shareId: string) {
  return api.post<ShareResponse>(`/shares/${shareId}/expire`)
}

export interface ListSharesParams {
  box: 'outbox' | 'inbox'
  q?: string
  state?: string[]
  recipient_user_id?: number
  recipient_group_id?: number
  sender_user_id?: number
  via_group_id?: number
  sort?: string
  direction?: 'asc' | 'desc'
  page?: number
  page_size?: number
}

export function listShares(params: ListSharesParams) {
  return api.get<ShareListResponse>('/shares', { params })
}

export function getShare(shareId: string) {
  return api.get<ShareResponse>(`/shares/${shareId}`)
}

export function deleteShare(shareId: string) {
  return api.delete(`/shares/${shareId}`)
}
