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
  /** ISO datetime, or null = never-expire (v1.1.4). */
  expires_at: string | null
  subject?: string | null
  message?: string | null
  public_link?: PublicLinkOnCreate | null
  notify_recipients?: boolean | null
  /** v1.1.0 per-share download limit. Omit / null = unlimited. */
  download_limit?: number | null
}) {
  return api.post<ShareResponse>('/shares', payload)
}

/** v1.1.4: pass `clear: true` to remove the expiry (share becomes
 *  never-expire); otherwise pass a new datetime. */
export function updateShareExpiry(
  shareId: string,
  opts: { expires_at?: string; clear?: boolean },
) {
  const body: Record<string, unknown> = {}
  if (opts.clear) body.expires_at_clear = true
  else if (opts.expires_at) body.expires_at = opts.expires_at
  return api.patch<ShareResponse>(`/shares/${shareId}`, body)
}

/** v1.1.0: PATCH the download budget. Pass `clear: true` to reset
 *  to unlimited; otherwise pass a positive limit. */
export function updateShareDownloadLimit(
  shareId: string,
  opts: { limit?: number | null; clear?: boolean },
) {
  return api.patch<ShareResponse>(`/shares/${shareId}`, {
    download_limit: opts.limit ?? null,
    download_limit_clear: opts.clear ?? false,
  })
}

export function expireShareNow(shareId: string) {
  return api.post<ShareResponse>(`/shares/${shareId}/expire`)
}

export interface BulkExpireResult {
  expired: string[]
  failed: { id: string; code: string; message: string }[]
}

export function bulkExpireShares(shareIds: string[]) {
  return api.post<BulkExpireResult>('/shares/bulk-expire', { share_ids: shareIds })
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
