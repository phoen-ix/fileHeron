import api, { createBareClient } from './client'
import type {
  CreatePublicLinkRequest,
  CreatePublicLinkResponse,
  PublicDownloadTicketResponse,
  PublicLinkResponse,
  PublicShareResponse,
  UpdatePublicLinkRequest,
} from '@/types/api'

/* Authed: managed from ShareDetail. */

export function createPublicLink(shareId: string, payload: CreatePublicLinkRequest) {
  return api.post<CreatePublicLinkResponse>(`/shares/${shareId}/public-link`, payload)
}

export function getPublicLink(shareId: string) {
  return api.get<PublicLinkResponse>(`/shares/${shareId}/public-link`)
}

/** Change the download limit; the link keeps its address. `clear` = unlimited. */
export function updatePublicLink(shareId: string, opts: { limit: number | null; clear: boolean }) {
  const body: UpdatePublicLinkRequest = {
    download_limit: opts.clear ? null : opts.limit,
    download_limit_clear: opts.clear,
  }
  return api.patch<PublicLinkResponse>(`/shares/${shareId}/public-link`, body)
}

export function revokePublicLink(shareId: string) {
  return api.delete(`/shares/${shareId}/public-link`)
}

/* Anonymous: the user-facing URL is /d/{token} (handled by the SPA),
 * but XHR for metadata + downloads goes through /api/public/{token}
 * (handled by the backend). The two paths must be distinct so the
 * proxy can route SPA-shell requests to nginx and JSON requests to
 * FastAPI. */

// The unlock cookie rides on subsequent calls.
const publicClient = createBareClient('/', { withCredentials: true })

export function fetchPublicShare(token: string) {
  return publicClient.get<PublicShareResponse>(`/api/public/${token}`)
}

export function unlockPublicShare(token: string, password: string) {
  return publicClient.post<{ ok: boolean }>(`/api/public/${token}/unlock`, { password })
}

/** The URL a Download click navigates to. A download through a link is
 * charged, and a mail link scanner renders the mailed page and fetches every
 * URL it links to - so the page links to no download at all: a click POSTs for
 * a short-lived URL (routers/public.py `download-ticket`). The unlock cookie
 * rides on the POST. `fileId` null = every file as one ZIP. */
export function getPublicDownloadUrl(token: string, fileId: string | null) {
  return publicClient.post<PublicDownloadTicketResponse>(`/api/public/${token}/download-ticket`, {
    file_id: fileId,
  })
}

export function publicPreviewUrl(token: string, fileId: string): string {
  // Inline preview source for <img>/<iframe> (or text fetch). The unlock
  // cookie rides on the same-origin subresource request (path-scoped to
  // /api/public/{token}); the endpoint serves it inline without consuming
  // the link's download budget.
  return `/api/public/${token}/files/${fileId}/preview`
}
