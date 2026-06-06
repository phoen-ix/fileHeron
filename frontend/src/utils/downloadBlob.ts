/** Trigger a browser "Save as" for a Blob.
 *
 * Admin CSV exports hit bearer-gated endpoints. The access token lives only in
 * memory and is attached by the axios request interceptor — a plain
 * `<a href download>` browser navigation carries no Authorization header (and no
 * useful cookie: the refresh cookie is path-scoped to /api/auth), so it 401s.
 * The fix is to fetch the file through axios (`responseType: 'blob'`) and hand
 * the resulting Blob to this helper.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
