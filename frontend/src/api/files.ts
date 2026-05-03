import api from './client'

/** Mint a short-lived signed URL for the browser to navigate to.
 * The bearer token lives in memory only (not a cookie), so a plain
 * <a href> can't authenticate; we trade the bearer for a one-shot
 * `?dt=` token that the download endpoint accepts. */
export function getDownloadUrl(fileId: string) {
  return api.get<{ url: string }>(`/files/${fileId}/download-url`)
}

export function deleteFile(fileId: string) {
  return api.delete(`/files/${fileId}`)
}
