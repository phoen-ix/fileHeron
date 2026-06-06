import api from './client'
import axios from 'axios'
import type {
  CreatePublicLinkRequest,
  CreatePublicLinkResponse,
  PublicLinkResponse,
  PublicShareResponse,
} from '@/types/api'

/* Authed: managed from ShareDetail. */

export function createPublicLink(
  shareId: string,
  payload: CreatePublicLinkRequest,
) {
  return api.post<CreatePublicLinkResponse>(
    `/shares/${shareId}/public-link`,
    payload,
  )
}

export function getPublicLink(shareId: string) {
  return api.get<PublicLinkResponse>(`/shares/${shareId}/public-link`)
}

export function revokePublicLink(shareId: string) {
  return api.delete(`/shares/${shareId}/public-link`)
}

/* Anonymous: the user-facing URL is /d/{token} (handled by the SPA),
 * but XHR for metadata + downloads goes through /api/public/{token}
 * (handled by the backend). The two paths must be distinct so the
 * proxy can route SPA-shell requests to nginx and JSON requests to
 * FastAPI. */

const publicClient = axios.create({
  baseURL: '/',
  withCredentials: true, // unlock cookie rides on subsequent calls
})

export function fetchPublicShare(token: string) {
  return publicClient.get<PublicShareResponse>(`/api/public/${token}`)
}

export function unlockPublicShare(token: string, password: string) {
  return publicClient.post<{ ok: boolean }>(
    `/api/public/${token}/unlock`,
    { password },
  )
}

export function publicDownloadUrl(token: string, fileId: string): string {
  // Browser-driven anchor. Cookies are auto-attached (path scoped to
  // /api/public/{token}); the server's Content-Disposition forces the
  // download UI.
  return `/api/public/${token}/files/${fileId}/download`
}

export function publicPreviewUrl(token: string, fileId: string): string {
  // Inline preview source for <img>/<iframe> (or text fetch). The unlock
  // cookie rides on the same-origin subresource request (path-scoped to
  // /api/public/{token}); the endpoint serves it inline without consuming
  // the link's download budget.
  return `/api/public/${token}/files/${fileId}/preview`
}

export function publicZipUrl(token: string): string {
  // Bulk-ZIP of every downloadable file in the share. Same unlock-cookie
  // gate as the single-file anchor above.
  return `/api/public/${token}/download-zip`
}
