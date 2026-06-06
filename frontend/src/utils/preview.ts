/** Mirror of backend `services/preview.py` — which file types render inline in
 *  the browser, and how. Keep this allowlist in sync with that module. SVG is
 *  deliberately excluded (it can carry script); text of any flavour renders as
 *  plaintext source. */
const PREVIEWABLE_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']

export type PreviewKind = 'image' | 'pdf' | 'text'

export function previewKind(mimeType: string | null | undefined): PreviewKind | null {
  if (!mimeType) return null
  const mime = mimeType.split(';', 1)[0].trim().toLowerCase()
  if (PREVIEWABLE_IMAGE_TYPES.includes(mime)) return 'image'
  if (mime === 'application/pdf') return 'pdf'
  if (mime.startsWith('text/')) return 'text'
  return null
}

/** Text previews are fetched fully into memory, so cap them — a huge "text"
 *  file would otherwise lock up the tab. Over this the modal shows a
 *  download-instead hint instead of loading. */
export const TEXT_PREVIEW_MAX_BYTES = 2 * 1024 * 1024
