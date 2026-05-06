import api from './client'
import type {
  ActivateInviteRequest,
  AdminApiTokenItem,
  AdminApiTokenListResponse,
  AdminAuditResponse,
  AdminCreateApiTokenRequest,
  AdminFileListResponse,
  AdminInviteListResponse,
  AdminInviteState,
  AdminUserItem,
  AdminUserListResponse,
  CreateApiTokenResponse,
  EmailSettingsResponse,
  EraseUserResponse,
  ForcePasswordResetResponse,
  HomePageSettingsResponse,
  PublicLinkPolicyResponse,
  QuarantineActionRequest,
  QuarantineSettingsResponse,
  RegenerateInviteResponse,
  ResendInviteResponse,
  ShareDefaultsResponse,
  SiteSettingsResponse,
  TestEmailRequest,
  TestEmailResponse,
  TokenPolicyResponse,
  TwofaPolicyResponse,
  UpdateEmailSettingsRequest,
  UpdateHomePageSettingsRequest,
  UpdatePublicLinkPolicyRequest,
  UpdateQuarantineSettingsRequest,
  UpdateShareDefaultsRequest,
  UpdateSiteSettingsRequest,
  UpdateTokenPolicyRequest,
  UpdateTwofaPolicyRequest,
  UpdateUserRequest,
  UserRole,
} from '@/types/api'

export function listUsers(params: {
  q?: string
  role?: UserRole
  page?: number
  page_size?: number
} = {}) {
  return api.get<AdminUserListResponse>('/admin/users', { params })
}

export function listInvites(
  params: {
    state?: AdminInviteState | 'all'
    page?: number
    page_size?: number
  } = {},
) {
  return api.get<AdminInviteListResponse>('/admin/invites', { params })
}

export function revokeInvite(id: number) {
  return api.delete(`/admin/invites/${id}`)
}

export function regenerateInvite(id: number) {
  return api.post<RegenerateInviteResponse>(`/admin/invites/${id}/regenerate`)
}

export function resendInvite(id: number) {
  return api.post<ResendInviteResponse>(`/admin/invites/${id}/resend`)
}

export function activateInvite(id: number, payload: ActivateInviteRequest) {
  return api.post<AdminUserItem>(`/admin/invites/${id}/activate`, payload)
}

export function getUser(id: number) {
  return api.get<AdminUserItem>(`/admin/users/${id}`)
}

export function updateUser(id: number, payload: UpdateUserRequest) {
  return api.patch<AdminUserItem>(`/admin/users/${id}`, payload)
}

export function forcePasswordReset(id: number) {
  return api.post<ForcePasswordResetResponse>(
    `/admin/users/${id}/force-password-reset`,
  )
}

export function eraseUser(id: number) {
  return api.post<EraseUserResponse>(`/admin/users/${id}/erase`)
}

export function listAuditLog(params: {
  event_type?: string
  actor_user_id?: number
  target_type?: string
  target_id?: string
  from?: string
  to?: string
  page?: number
  page_size?: number
} = {}) {
  return api.get<AdminAuditResponse>('/admin/audit-log', { params })
}

export function auditCsvUrl(params: Record<string, string> = {}): string {
  const sp = new URLSearchParams(params)
  const qs = sp.toString()
  return `/api/admin/audit-log/export.csv${qs ? `?${qs}` : ''}`
}

// API token policy + admin inventory (post-Phase 10)

export function getTokenPolicy() {
  return api.get<TokenPolicyResponse>('/admin/settings/api-tokens/policy')
}

export function updateTokenPolicy(payload: UpdateTokenPolicyRequest) {
  return api.put<TokenPolicyResponse>(
    '/admin/settings/api-tokens/policy',
    payload,
  )
}

export function adminListApiTokens(params: {
  q?: string
  owner_id?: number
  status?: 'active' | 'disabled' | 'revoked'
  page?: number
  page_size?: number
} = {}) {
  return api.get<AdminApiTokenListResponse>('/admin/api-tokens', { params })
}

export function adminCreateApiToken(payload: AdminCreateApiTokenRequest) {
  return api.post<CreateApiTokenResponse>('/admin/api-tokens', payload)
}

export function adminDisableApiToken(id: number) {
  return api.post<AdminApiTokenItem>(`/admin/api-tokens/${id}/disable`)
}

export function adminReactivateApiToken(id: number) {
  return api.post<AdminApiTokenItem>(`/admin/api-tokens/${id}/reactivate`)
}

export function adminRevokeApiToken(id: number) {
  return api.delete(`/admin/api-tokens/${id}`)
}

// Admin file history (post-Phase 10)

export function adminListFiles(params: {
  q?: string
  state?: string
  uploader_id?: number
  share_state?: string
  from?: string
  to?: string
  sort?: string
  direction?: 'asc' | 'desc'
  page?: number
  page_size?: number
} = {}) {
  return api.get<AdminFileListResponse>('/admin/files', { params })
}

// Public link policy (post-Phase 10)

export function getPublicLinkPolicy() {
  return api.get<PublicLinkPolicyResponse>('/admin/settings/public-links/policy')
}

export function updatePublicLinkPolicy(payload: UpdatePublicLinkPolicyRequest) {
  return api.put<PublicLinkPolicyResponse>(
    '/admin/settings/public-links/policy',
    payload,
  )
}

// Email / SMTP settings (post-Phase 10)

export function getEmailSettings() {
  return api.get<EmailSettingsResponse>('/admin/settings/email')
}

export function updateEmailSettings(payload: UpdateEmailSettingsRequest) {
  return api.put<EmailSettingsResponse>('/admin/settings/email', payload)
}

export function testEmailSend(payload: TestEmailRequest) {
  return api.post<TestEmailResponse>('/admin/settings/email/test', payload)
}

// Home page enable/disable (post-Phase 10)

export function getHomePageSettings() {
  return api.get<HomePageSettingsResponse>('/admin/settings/home-page')
}

export function updateHomePageSettings(payload: UpdateHomePageSettingsRequest) {
  return api.put<HomePageSettingsResponse>('/admin/settings/home-page', payload)
}

// Site URL (kv override of APP_URL env)

export function getSiteSettings() {
  return api.get<SiteSettingsResponse>('/admin/settings/site')
}

export function updateSiteSettings(payload: UpdateSiteSettingsRequest) {
  return api.put<SiteSettingsResponse>('/admin/settings/site', payload)
}

// 2FA enforcement policy (post-Phase 10)

export function getTwofaPolicy() {
  return api.get<TwofaPolicyResponse>('/admin/settings/twofa')
}

export function updateTwofaPolicy(payload: UpdateTwofaPolicyRequest) {
  return api.put<TwofaPolicyResponse>('/admin/settings/twofa', payload)
}

// Quarantine — admin actions on infected files + notification toggle

export function adminQuarantineRelease(fileId: string, payload: QuarantineActionRequest) {
  return api.post<void>(`/admin/files/${fileId}/quarantine/release`, payload)
}

export function adminQuarantinePurge(fileId: string) {
  return api.delete<void>(`/admin/files/${fileId}/quarantine`)
}

export function adminQuarantineDownloadUrl(fileId: string): string {
  return `/api/admin/files/${fileId}/quarantine/download`
}

export function getQuarantineSettings() {
  return api.get<QuarantineSettingsResponse>('/admin/settings/quarantine')
}

export function updateQuarantineSettings(payload: UpdateQuarantineSettingsRequest) {
  return api.put<QuarantineSettingsResponse>('/admin/settings/quarantine', payload)
}

export function getShareDefaults() {
  return api.get<ShareDefaultsResponse>('/admin/settings/share-defaults')
}

export function updateShareDefaults(payload: UpdateShareDefaultsRequest) {
  return api.put<ShareDefaultsResponse>('/admin/settings/share-defaults', payload)
}
