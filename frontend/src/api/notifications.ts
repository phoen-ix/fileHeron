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

export function deleteNotification(id: number) {
  return api.delete<MarkReadResponse>(`/notifications/${id}`)
}

export function deleteAllNotifications() {
  return api.delete<MarkReadResponse>('/notifications')
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
