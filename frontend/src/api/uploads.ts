import api from './client'
import type { DirectUploadResponse, UploadInitResponse } from '@/types/api'

export function initUpload(payload: {
  share_id: string
  filename: string
  size_bytes: number
  mime_type?: string
  /** The folder the file came from (folder uploads), e.g. 'Photos/2026'. */
  relative_dir?: string
}) {
  return api.post<UploadInitResponse>('/uploads/init', payload)
}

export function directUpload(
  shareId: string,
  file: File,
  onProgress?: (n: number) => void,
  relativeDir?: string | null,
) {
  const form = new FormData()
  form.append('share_id', shareId)
  // Only for a file that came from a folder (folder uploads).
  if (relativeDir) form.append('relative_dir', relativeDir)
  form.append('file', file)
  return api.post<DirectUploadResponse>('/uploads/direct', form, {
    // A direct upload can easily exceed the shared client's 30s default; the
    // whole-request timeout would abort a legit slow upload. Rely on progress
    // events / user abort instead.
    timeout: 0,
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: onProgress
      ? (e) => {
          if (e.total) onProgress(Math.round((e.loaded / e.total) * 100))
        }
      : undefined,
  })
}
