import api from './client'

/** Mint a short-lived signed URL for the browser to navigate to.
 * The bearer token lives in memory only (not a cookie), so a plain
 * <a href> can't authenticate; we trade the bearer for a one-shot
 * `?dt=` token that the download endpoint accepts. */
export function getDownloadUrl(fileId: string) {
  return api.get<{ url: string }>(`/files/${fileId}/download-url`)
}

/** Mint a one-shot signed URL for a bulk-ZIP of every downloadable file in a
 * share (same `?dt=` mechanism as a single file, bound to the share id). */
export function getShareZipUrl(shareId: string) {
  return api.get<{ url: string }>(`/files/${shareId}/download-zip-url`)
}

/** Mint a short-lived signed URL that serves the file INLINE for preview
 * (`<img>`/`<iframe>` src, or fetched as text). Same `?dt=` mechanism as
 * download; the preview endpoint never consumes the share's download budget. */
export function getPreviewUrl(fileId: string) {
  return api.get<{ url: string }>(`/files/${fileId}/preview-url`)
}

export function deleteFile(fileId: string) {
  return api.delete(`/files/${fileId}`)
}
