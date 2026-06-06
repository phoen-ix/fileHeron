import type { AxiosRequestConfig } from 'axios'

import api from './client'
import type {
  AdminNavCollapseMode,
  Locale,
  MeResponse,
  SessionListResponse,
  UserRole,
} from '@/types/api'

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

export function getMe(config?: AxiosRequestConfig) {
  return api.get<MeResponse>('/account/me', config)
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

/** Admin-only: set the sidebar collapse mode. `null` clears to the system
 *  default (accordion). The backend resets the open-set on a mode change. */
export function updateAdminNavMode(mode: AdminNavCollapseMode | null) {
  return api.patch<MeResponse>('/account/admin-nav-mode', { mode })
}

/** Admin-only: persist the set of open sidebar category keys (synced across
 *  devices). An empty array is a valid "all collapsed" value. */
export function updateAdminNavOpenCategories(open: string[]) {
  return api.patch<MeResponse>('/account/admin-nav-open', { open })
}

export function changePassword(payload: { current_password: string; new_password: string }) {
  return api.post('/account/change-password', payload)
}

/** Self-service email change (gated on the `email_change.self_service` policy). */
export function requestEmailChange(payload: { new_email: string; current_password: string }) {
  return api.post<{ ok: boolean; applied: boolean; mode: string }>(
    '/account/email',
    payload,
  )
}

// Sessions live under /auth (not /account) so the refresh cookie — which is
// path-scoped to /api/auth — is sent, letting the backend flag the current
// session and keep it on "sign out others".
export function listSessions() {
  return api.get<SessionListResponse>('/auth/sessions')
}

export function revokeSession(id: number) {
  return api.delete(`/auth/sessions/${id}`)
}

/** Revoke every session except the current device. */
export function revokeOtherSessions() {
  return api.post<{ revoked: number }>('/auth/sessions/revoke-others')
}

export function inviteUser(payload: InviteRequest) {
  return api.post<InviteResponse>('/account/invite', payload)
}
