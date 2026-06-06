import api from './client'
import type {
  ActivateInviteRequest,
  AdminApiTokenItem,
  AdminApiTokenListResponse,
  AdminAuditResponse,
  AnalyticsResponse,
  EmailTemplatesListResponse,
  EmailTemplateItem,
  UpdateEmailTemplateRequest,
  PreviewEmailTemplateRequest,
  PreviewEmailTemplateResponse,
  TestSendEmailTemplateRequest,
  TestSendEmailTemplateResponse,
  ImapSettingsResponse,
  UpdateImapSettingsRequest,
  ImapTestResponse,
  ImapFetchNowResponse,
  InboxListResponse,
  InboxDetail,
  UpdateInboxStatusRequest,
  CronListResponse,
  CronScheduleItem,
  UpdateCronScheduleRequest,
  WebhookItem,
  WebhookCreateResponse,
  WebhookDeliveryItem,
  AdminChangeEmailRequest,
  AdminChangeEmailResponse,
  AdminCreateApiTokenRequest,
  AdminFileListResponse,
  AdminMailDetail,
  AdminMailListResponse,
  AdminMailResendResponse,
  AdminInviteListResponse,
  AdminInviteState,
  AdminSessionListResponse,
  AdminUserItem,
  AdminUserListResponse,
  CreateApiTokenResponse,
  CreateUserRequest,
  EmailChangePolicyResponse,
  EmailSettingsResponse,
  AvReloadResponse,
  AvStatusResponse,
  EraseUserResponse,
  FilePreviewSettingsResponse,
  ForcePasswordResetResponse,
  HomePageSettingsResponse,
  PublicLinkPolicyResponse,
  QuarantineActionRequest,
  QuarantineSettingsResponse,
  RegenerateInviteResponse,
  ResendInviteResponse,
  ShareApprovalSettingsResponse,
  ShareDefaultsResponse,
  SiteSettingsResponse,
  TestEmailRequest,
  TestEmailResponse,
  TokenPolicyResponse,
  TwofaPolicyResponse,
  UpdateEmailChangePolicyRequest,
  UpdateEmailSettingsRequest,
  UpdateFilePreviewSettingsRequest,
  UpdateHomePageSettingsRequest,
  UpdatePublicLinkPolicyRequest,
  UpdateQuarantineSettingsRequest,
  UpdateShareApprovalSettingsRequest,
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

// --- MOTD ----------------------------------------------------------------

export interface MotdSettingsResponse {
  enabled: boolean
  text: string
}

export interface UpdateMotdSettingsRequest {
  enabled: boolean
  text: string
}

export function getMotdSettings() {
  return api.get<MotdSettingsResponse>('/admin/settings/motd')
}

export function updateMotdSettings(payload: UpdateMotdSettingsRequest) {
  return api.put<MotdSettingsResponse>('/admin/settings/motd', payload)
}


// --- Updates (release-check) settings ------------------------------------
// Cadence/enable for the release-check cron lives on the Scheduled tasks page.

export interface UpdatesSettingsResponse {
  api_url: string
}

export interface UpdateUpdatesSettingsRequest {
  api_url: string
}

export function getUpdatesSettings() {
  return api.get<UpdatesSettingsResponse>('/admin/settings/updates')
}

export function updateUpdatesSettings(payload: UpdateUpdatesSettingsRequest) {
  return api.put<UpdatesSettingsResponse>('/admin/settings/updates', payload)
}

// --- Advanced (registry-driven) settings ---

export type AdvancedSettingKind = 'int' | 'bool' | 'str'

export interface AdvancedSettingItem {
  key: string
  group: string
  kind: AdvancedSettingKind
  value: number | boolean | string
  default: number | boolean | string
  is_overridden: boolean
  min: number | null
  max: number | null
}

export interface AdvancedSettingsResponse {
  items: AdvancedSettingItem[]
}

export interface UpdateAdvancedSettingsRequest {
  // {key: value} to set, or {key: null} to reset that key to its default.
  updates: Record<string, number | boolean | string | null>
}

export function getAdvancedSettings() {
  return api.get<AdvancedSettingsResponse>('/admin/settings/advanced')
}

export function updateAdvancedSettings(payload: UpdateAdvancedSettingsRequest) {
  return api.put<AdvancedSettingsResponse>('/admin/settings/advanced', payload)
}

export interface CheckUpdatesResult {
  ok: boolean
  skipped?: string
  latest_version?: string
  admins_notified?: number
  url?: string
  error?: string
}

export function checkUpdatesNow() {
  return api.post<CheckUpdatesResult>('/admin/system/check-updates')
}


// --- System / ops view (operational audit) -------------------------------

export interface CronRunDTO {
  id: number
  job_name: string
  started_at: string | null
  completed_at: string | null
  status: 'running' | 'success' | 'failure'
  duration_ms: number | null
  result_summary: Record<string, unknown> | null
  error_msg: string | null
}

export interface LiveChecks {
  /** Server time the probes ran (naive UTC ISO) - for "checked <time>". */
  checked_at: string | null
  db: { status: string; error: string | null }
  redis: { status: string; error: string | null }
  av: { status: string; error: string | null }
}

export interface SystemStatusResponse {
  live: LiveChecks
  crons: Array<{
    job_name: string
    last_run: CronRunDTO | null
    last_24h: { success: number; failure: number; running: number }
  }>
  recent_failures: CronRunDTO[]
  email_undeliverable_24h: number
  /** Self-update surface. `running` + `sha` are baked into the image;
   * `latest` + everything below come from the hourly `release_check`
   * cron polling the GitHub releases API. All `latest*` fields are null
   * until the first successful poll. */
  version: {
    running: string
    sha: string
    /** GitHub release page for the running tag (null for dev builds /
     * non-github mirrors). The latest version's changelog uses release_url. */
    running_release_url: string | null
    latest: string | null
    update_available: boolean
    /** Every attempt (success OR failure) - display as "checked X ago". */
    last_check_at: string | null
    /** Only successful attempts - used to gate retries server-side. */
    last_success_at: string | null
    last_check_error: string | null
    release_notes: string | null
    release_url: string | null
    release_published_at: string | null
  }
}

export function getSystemStatus() {
  return api.get<SystemStatusResponse>('/admin/system/status')
}

export function getCronRuns(params: { job_name?: string; limit?: number } = {}) {
  return api.get<{ items: CronRunDTO[]; limit: number }>(
    '/admin/system/cron-runs',
    { params },
  )
}

/** Enqueue a scheduled cron to run now on the worker. The status table
 * updates via the existing SSE 'cron_run' event when it finishes. */
export function runCron(jobName: string) {
  return api.post<{ job_name: string; queued: boolean }>(
    `/admin/system/crons/${encodeURIComponent(jobName)}/run`,
  )
}

/** On-demand re-run of the liveness probes (db / redis / av). */
export function runLiveChecks() {
  return api.get<{ live: LiveChecks }>('/admin/system/live')
}


// Phase 4 - self-update.

export interface UpdaterStatus {
  current_tag: string
  rollback_target: string | null
  job_in_progress: string | null
}

export interface UpdaterJob {
  id: string
  action: 'update' | 'rollback'
  target_tag: string
  state: 'queued' | 'pulling' | 'restarting' | 'rolling_back' | 'healthy' | 'rolled_back' | 'failed'
  started_at: string
  finished_at: string | null
  log_tail: string[]
  error: string | null
  previous_tag: string | null
  rollback_reason: string | null
}

export function getUpdaterStatus() {
  return api.get<UpdaterStatus>('/admin/system/update-status')
}

export function getUpdaterJob(jobId: string) {
  return api.get<UpdaterJob>(`/admin/system/update-jobs/${jobId}`)
}

export function applyUpdate(password: string, target_tag: string) {
  return api.post<{ job_id: string; action: string; target_tag: string }>(
    '/admin/system/update',
    { password, target_tag },
  )
}

export function applyRollback(password: string) {
  return api.post<{ job_id: string; action: string; target_tag: string }>(
    '/admin/system/rollback',
    { password },
  )
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

/** Create a user immediately - no invite, email pre-verified, set password. */
export function createUserDirect(payload: CreateUserRequest) {
  return api.post<AdminUserItem>('/admin/users', payload)
}

export function updateUser(id: number, payload: UpdateUserRequest) {
  return api.patch<AdminUserItem>(`/admin/users/${id}`, payload)
}

export function forcePasswordReset(id: number) {
  return api.post<ForcePasswordResetResponse>(
    `/admin/users/${id}/force-password-reset`,
  )
}

export function changeUserEmail(id: number, payload: AdminChangeEmailRequest) {
  return api.post<AdminChangeEmailResponse>(`/admin/users/${id}/email`, payload)
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
  cursor?: string
} = {}) {
  return api.get<AdminAuditResponse>('/admin/audit-log', { params })
}

// Outbound webhooks (v1.19.0).
export function listWebhooks() {
  return api.get<WebhookItem[]>('/admin/webhooks')
}
export function getWebhookEvents() {
  return api.get<{ events: string[] }>('/admin/webhooks/events')
}
export function createWebhook(payload: { name: string; url: string; event_types: string[] }) {
  return api.post<WebhookCreateResponse>('/admin/webhooks', payload)
}
export function updateWebhook(
  id: number,
  payload: Partial<{ name: string; url: string; event_types: string[]; active: boolean }>,
  rotateSecret = false,
) {
  return api.patch<WebhookCreateResponse>(
    `/admin/webhooks/${id}${rotateSecret ? '?rotate_secret=true' : ''}`,
    payload,
  )
}
export function deleteWebhook(id: number) {
  return api.delete(`/admin/webhooks/${id}`)
}
export function testWebhook(id: number) {
  return api.post(`/admin/webhooks/${id}/test`)
}
export function listWebhookDeliveries(id: number) {
  return api.get<WebhookDeliveryItem[]>(`/admin/webhooks/${id}/deliveries`)
}
export function retryWebhookDelivery(deliveryId: number) {
  return api.post(`/admin/webhook-deliveries/${deliveryId}/retry`)
}

// Analytics dashboard (v1.18.0). CSV goes through axios (responseType blob) so
// the bearer is attached - a plain <a href> can't authenticate an admin GET.
export function getAnalytics(days: number) {
  return api.get<AnalyticsResponse>('/admin/analytics', { params: { days } })
}

export function exportAnalyticsCsv(days: number) {
  return api.get('/admin/analytics/export.csv', {
    params: { days },
    responseType: 'blob',
  })
}

// CSV export goes through axios (responseType blob) so the in-memory bearer is
// attached - a plain <a href> can't authenticate a bearer-gated admin GET.
export function exportAuditCsv(params: Record<string, string> = {}) {
  return api.get('/admin/audit-log/export.csv', { params, responseType: 'blob' })
}

// Mail log (v1.11.0)

export function listMailLog(params: {
  q?: string
  recipient_email?: string
  recipient_user_id?: number
  category?: string
  status?: string
  from?: string
  to?: string
  page?: number
  page_size?: number
  cursor?: string
} = {}) {
  return api.get<AdminMailListResponse>('/admin/mail-log', { params })
}

export function getMailLogDetail(id: number) {
  return api.get<AdminMailDetail>(`/admin/mail-log/${id}`)
}

export function resendMailLog(id: number) {
  return api.post<AdminMailResendResponse>(`/admin/mail-log/${id}/resend`)
}

export function exportMailCsv(params: Record<string, string> = {}) {
  return api.get('/admin/mail-log/export.csv', { params, responseType: 'blob' })
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
  status?: 'active' | 'disabled' | 'revoked' | 'expired'
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
  orphaned?: boolean
  include_inactive?: boolean
  from?: string
  to?: string
  sort?: string
  direction?: 'asc' | 'desc'
  page?: number
  page_size?: number
} = {}) {
  return api.get<AdminFileListResponse>('/admin/files', { params })
}

/** Free an orphaned file's bytes + the uploader's quota immediately. */
export function adminReclaimFile(fileId: string) {
  return api.post<void>(`/admin/files/${fileId}/reclaim`)
}

/** Admin hard-delete any file's bytes (frees quota, audits as admin). */
export function adminDeleteFile(fileId: string) {
  return api.delete<void>(`/admin/files/${fileId}`)
}

// Admin session oversight (v1.7.0)

export function adminListSessions(params: {
  q?: string
  user_id?: number
  include_inactive?: boolean
  sort?: 'created_at' | 'last_used_at' | 'expires_at'
  direction?: 'asc' | 'desc'
  page?: number
  page_size?: number
} = {}) {
  return api.get<AdminSessionListResponse>('/admin/sessions', { params })
}

export function adminRevokeSession(sessionId: number) {
  return api.delete<{ revoked: number }>(`/admin/sessions/${sessionId}`)
}

export function adminRevokeUserSessions(userId: number) {
  return api.delete<{ revoked: number }>(`/admin/users/${userId}/sessions`)
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

// In-browser file preview enable/disable (v1.23.0)

export function getFilePreviewSettings() {
  return api.get<FilePreviewSettingsResponse>('/admin/settings/file-preview')
}

export function updateFilePreviewSettings(
  payload: UpdateFilePreviewSettingsRequest,
) {
  return api.put<FilePreviewSettingsResponse>(
    '/admin/settings/file-preview',
    payload,
  )
}

// Share-approval policy (v1.24.0)

export function getShareApprovalSettings() {
  return api.get<ShareApprovalSettingsResponse>('/admin/settings/share-approval')
}

export function updateShareApprovalSettings(
  payload: UpdateShareApprovalSettingsRequest,
) {
  return api.put<ShareApprovalSettingsResponse>(
    '/admin/settings/share-approval',
    payload,
  )
}

// Site URL (kv override of APP_URL env)

export function getSiteSettings() {
  return api.get<SiteSettingsResponse>('/admin/settings/site')
}

export function updateSiteSettings(payload: UpdateSiteSettingsRequest) {
  return api.put<SiteSettingsResponse>('/admin/settings/site', payload)
}

// Branding (logo + surfaces + link) + legal pages (v1.31.0)

export interface BrandingLogoMeta {
  present: boolean
  filename: string | null
  content_type: string | null
  url: string | null
}

export interface BrandingSettingsResponse {
  logo: BrandingLogoMeta
  show_header: boolean
  show_login: boolean
  show_public: boolean
  show_email: boolean
  show_client: boolean
  link_url: string | null
}

export interface UpdateBrandingSettingsRequest {
  show_header?: boolean
  show_login?: boolean
  show_public?: boolean
  show_email?: boolean
  show_client?: boolean
  link_url?: string | null
}

export function getBrandingSettings() {
  return api.get<BrandingSettingsResponse>('/admin/settings/branding')
}

export function updateBrandingSettings(payload: UpdateBrandingSettingsRequest) {
  return api.put<BrandingSettingsResponse>('/admin/settings/branding', payload)
}

export function uploadBrandingLogo(file: File) {
  const form = new FormData()
  form.append('file', file)
  return api.post<BrandingSettingsResponse>('/admin/settings/branding/logo', form)
}

export function deleteBrandingLogo() {
  return api.delete<BrandingSettingsResponse>('/admin/settings/branding/logo')
}

export interface LegalDoc {
  enabled: boolean
  en: string
  de: string
}

export interface LegalSettingsResponse {
  imprint: LegalDoc
  privacy: LegalDoc
}

export function getLegalSettings() {
  return api.get<LegalSettingsResponse>('/admin/settings/legal')
}

export function updateLegalSettings(payload: LegalSettingsResponse) {
  return api.put<LegalSettingsResponse>('/admin/settings/legal', payload)
}

// 2FA enforcement policy (post-Phase 10)

export function getTwofaPolicy() {
  return api.get<TwofaPolicyResponse>('/admin/settings/twofa')
}

export function updateTwofaPolicy(payload: UpdateTwofaPolicyRequest) {
  return api.put<TwofaPolicyResponse>('/admin/settings/twofa', payload)
}

// Email-change policy (v1.13.0)

export function getEmailChangePolicy() {
  return api.get<EmailChangePolicyResponse>('/admin/settings/email-change')
}

export function updateEmailChangePolicy(payload: UpdateEmailChangePolicyRequest) {
  return api.put<EmailChangePolicyResponse>('/admin/settings/email-change', payload)
}

// Quarantine - admin actions on infected files + notification toggle

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

/* v1.1.6: AV engine read-only status + manual reload. */

export function getAvStatus() {
  return api.get<AvStatusResponse>('/admin/quarantine/av-status')
}

export function reloadAvSignatures() {
  return api.post<AvReloadResponse>('/admin/quarantine/av-reload')
}

export function getShareDefaults() {
  return api.get<ShareDefaultsResponse>('/admin/settings/share-defaults')
}

export function updateShareDefaults(payload: UpdateShareDefaultsRequest) {
  return api.put<ShareDefaultsResponse>('/admin/settings/share-defaults', payload)
}

// Admin-editable email templates (v1.25.0)
const _et = (slug: string, locale: string) =>
  `/admin/settings/email-templates/${encodeURIComponent(slug)}/${encodeURIComponent(locale)}`

export function getEmailTemplates() {
  return api.get<EmailTemplatesListResponse>('/admin/settings/email-templates')
}

export function getEmailTemplate(slug: string, locale: string) {
  return api.get<EmailTemplateItem>(_et(slug, locale))
}

export function updateEmailTemplate(
  slug: string,
  locale: string,
  payload: UpdateEmailTemplateRequest,
) {
  return api.put<EmailTemplateItem>(_et(slug, locale), payload)
}

export function resetEmailTemplate(slug: string, locale: string) {
  return api.delete<EmailTemplateItem>(_et(slug, locale))
}

export function previewEmailTemplate(
  slug: string,
  locale: string,
  payload: PreviewEmailTemplateRequest,
) {
  return api.post<PreviewEmailTemplateResponse>(`${_et(slug, locale)}/preview`, payload)
}

export function testSendEmailTemplate(
  slug: string,
  locale: string,
  payload: TestSendEmailTemplateRequest,
) {
  return api.post<TestSendEmailTemplateResponse>(`${_et(slug, locale)}/test-send`, payload)
}

// Inbound mailbox / IMAP (v1.27.0)
export function getImapSettings() {
  return api.get<ImapSettingsResponse>('/admin/settings/imap')
}

export function updateImapSettings(payload: UpdateImapSettingsRequest) {
  return api.put<ImapSettingsResponse>('/admin/settings/imap', payload)
}

export function testImap() {
  return api.post<ImapTestResponse>('/admin/settings/imap/test')
}

export function fetchInboxNow() {
  return api.post<ImapFetchNowResponse>('/admin/settings/imap/fetch-now')
}

export function listInbox(
  params: {
    q?: string
    classification?: string
    status?: string
    sender_email?: string
    page?: number
    page_size?: number
  } = {},
) {
  return api.get<InboxListResponse>('/admin/inbox', { params })
}

export function getInboxUnreadCount() {
  return api.get<{ unread: number }>('/admin/inbox/unread-count')
}

export function getInboxMessage(id: number) {
  return api.get<InboxDetail>(`/admin/inbox/${id}`)
}

export function updateInboxStatus(id: number, payload: UpdateInboxStatusRequest) {
  return api.patch<InboxDetail>(`/admin/inbox/${id}`, payload)
}

export function deleteInboxMessage(id: number) {
  return api.delete(`/admin/inbox/${id}`)
}

export function downloadInboxAttachment(msgId: number, attId: number) {
  return api.get(`/admin/inbox/${msgId}/attachments/${attId}/download`, {
    responseType: 'blob',
  })
}

// --- Scheduled tasks / crons (v1.28.0) -----------------------------------
export function getCrons() {
  return api.get<CronListResponse>('/admin/crons')
}

export function updateCronSchedule(name: string, payload: UpdateCronScheduleRequest) {
  return api.put<CronScheduleItem>(`/admin/crons/${encodeURIComponent(name)}`, payload)
}
