import api from './client'
import type { DirectUploadResponse, UploadInitResponse } from '@/types/api'

export function initUpload(payload: {
  share_id: string
  filename: string
  size_bytes: number
  mime_type?: string
}) {
  return api.post<UploadInitResponse>('/uploads/init', payload)
}

export function directUpload(shareId: string, file: File, onProgress?: (n: number) => void) {
  const form = new FormData()
  form.append('share_id', shareId)
  form.append('file', file)
  return api.post<DirectUploadResponse>('/uploads/direct', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: onProgress
      ? (e) => {
          if (e.total) onProgress(Math.round((e.loaded / e.total) * 100))
        }
      : undefined,
  })
}
