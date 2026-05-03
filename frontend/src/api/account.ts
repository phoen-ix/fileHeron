import api from './client'
import type { Locale, MeResponse, SessionListResponse, UserRole } from '@/types/api'

export interface InviteRequest {
  email: string
  display_name_hint: string
  target_role: UserRole
  initial_group_ids?: number[]
}

export interface InviteResponse {
  ok: boolean
  email: string
  expires_at: string
}

export function getMe() {
  return api.get<MeResponse>('/account/me')
}

export function updateLocale(locale: Locale) {
  return api.patch<MeResponse>('/account/locale', { locale })
}

export function updateDisplayName(display_name: string) {
  return api.patch<MeResponse>('/account/display-name', { display_name })
}

export function updateDefaultLandingPage(default_landing_page: string | null) {
  return api.patch<MeResponse>('/account/default-landing-page', {
    default_landing_page,
  })
}

export function changePassword(payload: { current_password: string; new_password: string }) {
  return api.post('/account/change-password', payload)
}

export function listSessions() {
  return api.get<SessionListResponse>('/account/sessions')
}

export function revokeSession(id: number) {
  return api.delete(`/account/sessions/${id}`)
}

export function inviteUser(payload: InviteRequest) {
  return api.post<InviteResponse>('/account/invite', payload)
}
