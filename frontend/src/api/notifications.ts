import api from './client'
import type {
  MarkReadResponse,
  NotificationCategory,
  NotificationChannel,
  NotificationListResponse,
  PreferencesResponse,
} from '@/types/api'

export function listNotifications(params: {
  unread?: boolean
  page?: number
  page_size?: number
} = {}) {
  return api.get<NotificationListResponse>('/notifications', { params })
}

export function markRead(id: number) {
  return api.post<MarkReadResponse>(`/notifications/${id}/read`)
}

export function markAllRead() {
  return api.post<MarkReadResponse>('/notifications/read-all')
}

export function getStreamToken() {
  return api.get<{ token: string }>('/notifications/stream-token')
}

export function getPreferences() {
  return api.get<PreferencesResponse>('/notifications/preferences')
}

export function updatePreferences(
  preferences: Record<NotificationCategory, NotificationChannel>,
) {
  return api.put<PreferencesResponse>('/notifications/preferences', {
    preferences,
  })
}
