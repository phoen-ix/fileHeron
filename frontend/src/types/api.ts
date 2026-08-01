/* TypeScript shapes mirroring the FastAPI Pydantic schemas. Hand-written
 * (not generated) - small surface, one place to update when the API shape
 * shifts. */

export type Locale = 'en' | 'de'
export type UserRole = 'admin' | 'employee' | 'client'
/** v1.15.0: how the admin sidebar's collapsible categories behave. */
export type AdminNavCollapseMode = 'expanded' | 'accordion' | 'manual'

export interface MeResponse {
  id: number
  email: string
  display_name: string
  role: UserRole
  locale: Locale
  email_verified: boolean
  is_disabled: boolean
  created_at: string
  last_login_at: string | null
  quota_bytes: number | null
  /** Post-Phase 10: derived from the public-link policy. SPA hides
   * the inline-create toggle in /share/new when False. */
  can_create_public_link: boolean
  /** Post-Phase 10: per-user post-login destination. Route name like
   * `outbox`, `inbox`, etc. or null = use system default. */
  default_landing_page: string | null
  /** Post-Phase 10: global flag from app_settings. When False, the
   * home page is hidden from the picker, the brand mark in
   * AppHeader is plain text, and `/` redirects forward. */
  home_page_enabled: boolean
  /** Post-Phase 10: True when the active 2FA policy applies to this
   * user and they haven't enabled TOTP yet. The router guard reads
   * this and bounces every navigation to /account/2fa/forced until
   * the user finishes setup (then it flips false on the next /me
   * fetch). */
  requires_2fa: boolean
  /** Post-Phase 10: default state of the per-share "Notify recipient(s)"
   * checkbox on the create-share form. Sourced from the kv
   * `share.notify_recipients_default` (admin-editable). */
  share_notify_recipients_default: boolean
  /** v1.13.0: whether self-service email change is enabled
   * (`email_change.self_service`). The Account page hides the
   * "Change email" block when false. */
  can_change_own_email: boolean
  /** v1.23.0: global in-browser-preview switch (`file_preview.enabled`,
   * admin-set, default true). When false the SPA hides every Preview
   * button; the preview endpoints also refuse server-side. */
  file_preview_enabled: boolean
  /** v1.24.0: true when the share-approval workflow is on AND this user is in
   * the approver set. Drives the Approvals nav entry + approve/reject UI. */
  can_approve_shares: boolean
  /** v1.15.0: per-admin collapsible-sidebar mode. null = system default
   * (accordion). Only meaningful for admins. */
  admin_nav_collapse_mode: AdminNavCollapseMode | null
  /** v1.15.0: open sidebar category keys, synced across devices. null =
   * never set (client uses the mode's default); [] = all collapsed. */
  admin_nav_open_categories: string[] | null
}

export interface ShareDefaultsResponse {
  notify_recipients_default: boolean
}

export interface UpdateShareDefaultsRequest {
  notify_recipients_default: boolean
}

export interface FilePreviewSettingsResponse {
  enabled: boolean
}

export interface UpdateFilePreviewSettingsRequest {
  enabled: boolean
}

export type ApproverMode = 'admins_only' | 'employees_admins'
export type ApprovalScope = 'outbound' | 'all' | 'outbound_to_clients'

export interface ApproverUserRef {
  id: number
  display_name: string
  email: string
  role: string
}

export interface ApproverGroupRef {
  id: number
  name: string
}

export interface ShareApprovalSettingsResponse {
  enabled: boolean
  approver_mode: ApproverMode
  approver_user_ids: number[]
  approver_group_ids: number[]
  approver_users: ApproverUserRef[]
  approver_groups: ApproverGroupRef[]
  scope: ApprovalScope
  exempt_approvers: boolean
  allow_content_review: boolean
  /** True when the saved policy can never queue anything - "every employee may
   *  approve" plus "approvers' own shares are exempt" cancel out. New saves are
   *  refused; this flags instances stored before the check existed. */
  is_inert?: boolean
}

export interface UpdateShareApprovalSettingsRequest {
  enabled: boolean
  approver_mode: ApproverMode
  approver_user_ids: number[]
  approver_group_ids: number[]
  scope: ApprovalScope
  exempt_approvers: boolean
  allow_content_review: boolean
}

export interface LoginResponse {
  access_token: string
  expires_in_seconds: number
}

export interface RefreshResponse {
  access_token: string
  expires_in_seconds: number
}

export interface ApiErrorEnvelope {
  error: string
  code: string
  details?: Record<string, unknown>
  request_id?: string
}

/* 2FA */

export interface TotpSetupResponse {
  secret_b32: string
  otpauth_uri: string
  qr_svg: string
}

export interface TotpStatusResponse {
  enabled: boolean
  enabled_at: string | null
  recovery_codes_remaining: number
}

export interface RecoveryCodesResponse {
  recovery_codes: string[]
}

/* Sessions */

export interface SessionRecord {
  id: number
  created_at: string
  last_used_at: string | null
  expires_at: string
  created_ip: string | null
  created_ua: string | null
  is_current: boolean
}

export interface SessionListResponse {
  items: SessionRecord[]
}

export interface AdminSessionRow {
  id: number
  user_id: number
  user_display_name: string | null
  user_email: string | null
  created_at: string
  last_used_at: string | null
  expires_at: string
  revoked_at: string | null
  created_ip: string | null
  created_ua: string | null
  is_active: boolean
}

export interface AdminSessionListResponse {
  items: AdminSessionRow[]
  total: number
  page: number
  page_size: number
}

/* User search (Phase 4 recipient picker) */

export interface UserSearchItem {
  user_id: number
  display_name: string
  email: string
  role: UserRole
}

export interface UserSearchResponse {
  items: UserSearchItem[]
}

/* Groups (Phase 4) */

export interface GroupResponse {
  id: number
  name: string
  description: string | null
  is_company_inbox: boolean
  created_at: string
  created_by_id: number
  member_count: number
}

export interface GroupMemberItem {
  user_id: number
  display_name: string
  email: string
  role: UserRole
  joined_at: string
}

export interface GroupDetailResponse extends GroupResponse {
  members: GroupMemberItem[]
}

export interface GroupListResponse {
  items: GroupResponse[]
}

export interface CreateGroupRequest {
  name: string
  description?: string | null
  is_company_inbox?: boolean
}

export interface UpdateGroupRequest {
  name?: string
  description?: string | null
  is_company_inbox?: boolean
}

/* Shares */

export type ShareKind = 'outbound' | 'inbound'
export type ShareState =
  | 'active'
  | 'expired'
  | 'revoked'
  | 'deleted'
  | 'failed'
  | 'pending_approval'
  | 'rejected'
// Mirrors backend app/models/file.py::FileState.
export type FileState =
  | 'uploading'
  | 'ready_unscanned'
  | 'clean'
  | 'infected'
  | 'deleted'

export interface FileInShareResponse {
  id: string
  original_filename: string
  mime_type: string
  size_bytes: number
  state: FileState
  created_at: string
  finalized_at: string | null
  sha256_hex: string | null
  /* True when the file is too large for clamd to scan, so it was released
   * without a real antivirus verdict. `state` is still 'clean' (it is
   * downloadable); this is what distinguishes "scanned and clean" from
   * "never scanned". Surface it, don't imply safety. */
  av_unscanned?: boolean
}

export interface GroupRecipientRef {
  id: number
  name: string
  is_company_inbox: boolean
}

export interface InlinePublicLinkResult {
  id: string
  url: string
  qr_svg?: string | null
  download_limit: number | null
  downloads_remaining: number | null
  notify_on_download: boolean
  has_password: boolean
  created_at: string
}

export interface ShareResponse {
  id: string
  kind: ShareKind
  state: ShareState
  subject: string | null
  /** Display fallback: subject if set, else first file's filename,
   *  else "" (frontend localises to "(no subject)"). */
  effective_subject: string
  message: string | null
  created_at: string
  /** ISO datetime, or null = never-expire (v1.1.4). SPA renders null
   *  as "Never" via formatExpiryInSiteTime. */
  expires_at: string | null
  created_by_id: number
  recipient_user_ids: number[]
  recipient_groups: GroupRecipientRef[]
  files: FileInShareResponse[]
  /** v1.1.0 per-share download budget. Both null = unlimited. */
  download_limit: number | null
  downloads_remaining: number | null
  /** Populated only when `public_link` was set in the create request. */
  public_link?: InlinePublicLinkResult | null
  /** v1.24.0 share-approval. `rejection_reason` set when state==='rejected';
   *  `viewer_can_approve` true when the current viewer may approve/reject this
   *  pending share now (approver, not their own). */
  rejection_reason?: string | null
  approval_decided_at?: string | null
  viewer_can_approve?: boolean
  /** Set for the owner, admins and approvers when a public link is attached.
   *  Never carries the URL - it exists so an approver can see that approving
   *  this share also publishes a world-readable link. */
  public_link_summary?: PublicLinkSummary | null
  /** Digest of the reviewed file set + attached link, present while pending.
   *  Echoed back on approve; a stale value is refused with 409. */
  content_fingerprint?: string | null
}

export interface PublicLinkSummary {
  has_password: boolean
  download_limit: number | null
  downloads_remaining: number | null
  created_at: string
}

export interface ShareRecipientsRequest {
  user_ids: number[]
  group_ids: number[]
}

export interface PublicLinkOnCreate {
  password?: string | null
  download_limit?: number | null
  notify_on_download?: boolean
}

export interface UpdateShareRequest {
  /** Omit = no change; ISO datetime = replace. Mutually exclusive with
   *  expires_at_clear. */
  expires_at?: string
  /** Send true to clear the expiry (share becomes never-expire, v1.1.4).
   *  Mutually exclusive with expires_at - sending both is a 400. */
  expires_at_clear?: boolean
}

/* Admin public-link policy (post-Phase 10) */

export type PublicLinkPolicyMode = TokenPolicyMode

export interface PublicLinkAllowedUserItem {
  id: number
  display_name: string
  email: string
  role: UserRole
}

export interface PublicLinkAllowedGroupItem {
  id: number
  name: string
}

export interface PublicLinkPolicyResponse {
  mode: PublicLinkPolicyMode
  allowed_user_ids: number[]
  allowed_group_ids: number[]
  allowed_users: PublicLinkAllowedUserItem[]
  allowed_groups: PublicLinkAllowedGroupItem[]
}

export interface UpdatePublicLinkPolicyRequest {
  mode: PublicLinkPolicyMode
  allowed_user_ids: number[]
  allowed_group_ids: number[]
}

/* Public links (Phase 5) */

export interface PublicLinkResponse {
  id: string
  /** Decrypted public URL for owner display. Null on legacy rows
   *  written before the encrypted-token column shipped. */
  url: string | null
  /** Inline SVG QR of the public URL. Null when there's no URL to encode. */
  qr_svg?: string | null
  download_limit: number | null
  downloads_remaining: number | null
  notify_on_download: boolean
  has_password: boolean
  locked_until: string | null
  revoked_at: string | null
  created_at: string
}

export interface CreatePublicLinkResponse extends PublicLinkResponse {
  /** Always set on create - narrows the parent's nullable url. */
  url: string
}

export interface CreatePublicLinkRequest {
  password?: string | null
  download_limit?: number | null
  notify_on_download?: boolean
}

export interface PublicShareFile {
  id: string
  original_filename: string
  mime_type: string
  size_bytes: number
  state: FileState
  /* True when the file is too large for clamd to scan, so it was released
   * without a real antivirus verdict. `state` is still 'clean' (it is
   * downloadable); this is what distinguishes "scanned and clean" from
   * "never scanned". Surface it, don't imply safety. */
  av_unscanned?: boolean
}

export interface PublicShareResponse {
  share_id: string
  subject: string | null
  message: string | null
  /** ISO datetime, or null = never-expire (v1.1.4). */
  expires_at: string | null
  requires_password: boolean
  unlocked: boolean
  downloads_remaining: number | null
  /** v1.23.0: global in-browser-preview switch. Gates the Preview buttons
   * in the anonymous /d/{token} view (the endpoint enforces it too). */
  preview_enabled: boolean
  files: PublicShareFile[]
}

/* Notifications + preferences (Phase 6a/6b) */

export type NotificationCategory =
  | 'share_created'
  | 'share_files_added'
  | 'share_expiring'
  | 'share_pending_approval'
  | 'share_approved'
  | 'share_rejected'
  | 'public_link_downloaded'
  | 'account_created'
  | 'reset_password'
  | 'login_alert'
  | 'oidc_linked'
  | 'file_quarantined'
  | 'session_evicted'
  | 'ops_alert'
  | 'release_available'
  | 'inbound_message'

export type NotificationChannel = 'off' | 'email' | 'in_app' | 'both'

export interface PreferenceItem {
  category: NotificationCategory
  channel: NotificationChannel
  locked: boolean
}

export interface PreferencesResponse {
  items: PreferenceItem[]
}

export interface SubscriptionContextResponse {
  display_name: string
  items: PreferenceItem[]
}

export interface UnsubscribeResponse {
  items: PreferenceItem[]
  category: string
  previous_channel: NotificationChannel
}

export interface NotificationItem {
  id: number
  category: NotificationCategory
  payload: Record<string, unknown>
  link_url: string | null
  created_at: string
  read_at: string | null
}

export interface NotificationListResponse {
  items: NotificationItem[]
  unread_count: number
  page: number
  page_size: number
  total: number
}

export interface MarkReadResponse {
  ok: boolean
  unread_count: number
}

/* Admin (Phase 6b) */

export interface AdminUserItem {
  id: number
  display_name: string
  email: string
  role: UserRole
  is_disabled: boolean
  /** Post-Phase 10: computed live from the 2FA policy + user's TOTP
   * state. Was a static column (`requires_2fa_setup`) before the
   * column was dropped because it wasn't kept consistent. */
  requires_2fa: boolean
  quota_bytes: number | null
  /** Live Redis quota counter - kept honest by the hourly
   * `quota_reconcile` cron. Useful for spotting who's eating disk on
   * the /admin/users list without drilling into file history. */
  storage_used_bytes: number
  created_at: string
  last_login_at: string | null
  has_2fa: boolean
  /** v1.13.0: drives the "verification pending" pill on the detail page. */
  email_verified: boolean
}

export interface AdminUserListResponse {
  items: AdminUserItem[]
  total: number
  page: number
  page_size: number
}

/* Admin pending-invites views (post-Phase 10). */

/** v1.1.5: 'revoked' dropped - admin delete is now a hard delete. */
export type AdminInviteState = 'pending' | 'expired'

export interface AdminInviteItem {
  id: number
  email: string
  target_role: UserRole
  state: AdminInviteState
  invited_by_id: number | null
  invited_by_display_name: string | null
  initial_group_ids: number[] | null
  created_at: string
  expires_at: string
}

export interface AdminInviteListResponse {
  items: AdminInviteItem[]
  total: number
  page: number
  page_size: number
}

export interface ActivateInviteRequest {
  display_name?: string
  locale?: Locale
}

export interface RegenerateInviteResponse {
  token: string
  url: string
  expires_at: string
}

export interface ResendInviteResponse {
  ok: boolean
  expires_at: string
}

export interface UpdateUserRequest {
  display_name?: string
  role?: UserRole
  quota_bytes?: number | null
  is_disabled?: boolean
}

/** Admin creates a user directly (no invite, email pre-verified, set password). */
export interface CreateUserRequest {
  email: string
  display_name: string
  password: string
  target_role: UserRole
  initial_group_ids?: number[]
}

export interface ForcePasswordResetResponse {
  plaintext_token: string
  expires_at: string
}

/* Email change (v1.13.0). */

export type EmailChangeVerificationMode = 'immediate' | 'verify_new' | 'verify_both'
export type EmailChangeOidcMode = 'reset_setpw' | 'reset_only' | 'keep'

export interface AdminChangeEmailRequest {
  new_email: string
  skip_verification?: boolean
}

export interface AdminChangeEmailResponse {
  applied: boolean
  mode: string
  oidc_reset: boolean
  set_password_token_issued: boolean
  confirm_url: string | null
  old_confirm_url: string | null
  user: AdminUserItem
}

export interface EmailChangePolicyResponse {
  verification_mode: EmailChangeVerificationMode
  self_service: boolean
  oidc_mode: EmailChangeOidcMode
}

export interface UpdateEmailChangePolicyRequest {
  verification_mode: EmailChangeVerificationMode
  self_service: boolean
  oidc_mode: EmailChangeOidcMode
}

export interface ErasePreflight {
  user_id: number
  display_name: string
  email: string
  role: string
  is_already_erased: boolean
  files_to_delete: number
  bytes_to_delete: number
  shares_created: number
  shares_received_to_anonymize: number
}

export interface EraseUserResponse {
  user_id: number
  deleted_files: number
  deleted_bytes: number
  erased_at: string
  /** Audit row the receipt PDF is generated from. */
  audit_id: number | null
  pii_purged: Record<string, number>
}

export interface AdminAuditRow {
  id: number
  event_type: string
  actor_user_id: number | null
  /** Hydrated server-side. Null for system / anonymous events
   *  AND for actors whose accounts were erased. */
  actor_display_name: string | null
  actor_email: string | null
  target_type: string | null
  target_id: string | null
  request_id: string | null
  ip: string | null
  extra: Record<string, unknown> | null
  created_at: string
}

export interface AdminAuditResponse {
  items: AdminAuditRow[]
  total: number
  page: number
  page_size: number
  /** Opaque cursor for the next-older page; null on the last page. */
  next_cursor: string | null
}

/* Admin analytics dashboard (v1.18.0). */
export interface AnalyticsDayPoint {
  date: string
  count: number
}
export interface AnalyticsStoragePoint {
  date: string
  storage_bytes: number
  files_clean: number
  files_infected: number
  files_total: number
}
export interface AnalyticsTopUploader {
  user_id: number
  display_name: string
  email: string
  bytes: number
}
export interface AnalyticsTopShare {
  share_id: string
  subject: string | null
  downloads: number
}
export interface AnalyticsQuotaWarning {
  user_id: number
  display_name: string
  email: string
  used_bytes: number
  quota_bytes: number
  pct: number
}
export interface AnalyticsResponse {
  days: number
  range: { from: string; to: string }
  storage_trend: AnalyticsStoragePoint[]
  storage_as_of: string | null
  shares_created: AnalyticsDayPoint[]
  downloads: AnalyticsDayPoint[]
  av_quarantines: AnalyticsDayPoint[]
  file_states: Record<string, number>
  top_uploaders: AnalyticsTopUploader[]
  top_shares: AnalyticsTopShare[]
  quota_warnings: AnalyticsQuotaWarning[]
}

/* Outbound webhooks (v1.19.0). */
export interface WebhookItem {
  id: number
  name: string
  url: string
  event_types: string[]
  active: boolean
  secret_set: boolean
  created_at: string
}
export interface WebhookCreateResponse extends WebhookItem {
  /** Plaintext signing secret - returned only on create / rotate. */
  secret: string
}
export interface WebhookDeliveryItem {
  id: number
  event_type: string
  status: 'pending' | 'sent' | 'failed'
  response_code: number | null
  attempts: number
  error: string | null
  created_at: string
  delivered_at: string | null
}

/* Mail log (v1.11.0) - outbound email send log. */
export interface AdminMailRow {
  id: number
  created_at: string
  recipient_email: string
  recipient_user_id: number | null
  /** Hydrated server-side; null for non-users / erased recipients. */
  recipient_display_name: string | null
  category: string | null
  template_slug: string | null
  via: string
  status: string
  subject: string
  /** True when auth-link tokens were redacted at rest → resend disabled. */
  masked: boolean
  attempts: number
  smtp_code: number | null
  error_class: string | null
  can_resend: boolean
}

export interface AdminMailDetail extends AdminMailRow {
  body_text: string | null
  body_html: string | null
  error_message: string | null
  source_log_id: number | null
}

export interface AdminMailListResponse {
  items: AdminMailRow[]
  total: number
  page: number
  page_size: number
  next_cursor: string | null
}

export interface AdminErrorRow {
  id: number
  created_at: string
  source: string
  status_code: number
  code: string
  exception_type: string | null
  message: string | null
  method: string | null
  path: string | null
  job_name: string | null
  ip: string | null
  request_id: string | null
  user_id: number | null
  auth_via: string | null
  signature: string
  /** True when an alert email actually went out for this row. */
  alerted: boolean
}

export interface AdminErrorListResponse {
  items: AdminErrorRow[]
  total: number
  page: number
  page_size: number
}

export interface AdminMailResendResponse {
  ok: boolean
  new_log_id: number
}

export interface ShareRecipientRef {
  kind: 'user' | 'group' | 'company'
  id: number
  label: string
  role?: string | null
}

export interface ShareSenderRef {
  id: number
  display_name: string
  email: string
}

export interface ShareListItem {
  id: string
  kind: ShareKind
  state: ShareState
  subject: string | null
  /** Display fallback: subject if set, else first file's filename,
   *  else "" (frontend localises to "(no subject)"). */
  effective_subject: string
  created_at: string
  /** ISO datetime, or null = never-expire (v1.1.4). */
  expires_at: string | null
  created_by_id: number
  file_count: number
  total_size_bytes: number
  /** v1.1.0 per-share download budget. Both null = unlimited. */
  download_limit: number | null
  downloads_remaining: number | null
  recipients: ShareRecipientRef[]
  sender: ShareSenderRef | null
}

export interface ShareListResponse {
  items: ShareListItem[]
  total: number
  page: number
  page_size: number
}

/* Uploads */

export interface UploadInitResponse {
  file_id: string
  tus_endpoint: string
  upload_metadata_header: string
  expires_at: string
}

export interface DirectUploadResponse {
  file_id: string
  size_bytes: number
  sha256_hex: string | null
}

/* API tokens */

export interface ApiTokenListItem {
  id: number
  name: string
  last4: string
  created_at: string
  last_used_at: string | null
  expires_at: string | null
  /** null = unrestricted (full access); else the granted scope names. */
  scopes: string[] | null
}

export interface ApiTokenListResponse {
  items: ApiTokenListItem[]
  /** Post-Phase 10: false → SPA hides the create form. */
  can_create: boolean
}

export interface CreateApiTokenResponse {
  id: number
  name: string
  last4: string
  plaintext_token: string
  created_at: string
  expires_at: string | null
  scopes: string[] | null
  owner_user_id?: number
  owner_display_name?: string
}

/* Admin API tokens (post-Phase 10) */

export type TokenPolicyMode =
  | 'everyone'
  | 'employees_admins'
  | 'admins_only'

export type TokenStatus = 'active' | 'disabled' | 'revoked' | 'expired'

export interface AllowedUserItem {
  id: number
  display_name: string
  email: string
  role: UserRole
}

export interface AllowedGroupItem {
  id: number
  name: string
}

export interface TokenPolicyResponse {
  mode: TokenPolicyMode
  allowed_user_ids: number[]
  allowed_group_ids: number[]
  allowed_users: AllowedUserItem[]
  allowed_groups: AllowedGroupItem[]
}

export interface UpdateTokenPolicyRequest {
  mode: TokenPolicyMode
  allowed_user_ids: number[]
  allowed_group_ids: number[]
}

export interface AdminApiTokenItem {
  id: number
  name: string
  last4: string
  owner_user_id: number
  owner_display_name: string
  owner_email: string
  owner_role: UserRole
  status: TokenStatus
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
  disabled_at: string | null
  expires_at: string | null
  scopes: string[] | null
}

export interface AdminApiTokenListResponse {
  items: AdminApiTokenItem[]
  total: number
  page: number
  page_size: number
}

export interface AdminCreateApiTokenRequest {
  target_user_id: number
  name: string
  expires_at?: string | null
  scopes?: string[] | null
}

/* Admin file history (post-Phase 10) */

export interface FileUploaderRef {
  id: number
  display_name: string
  email: string
  role: UserRole
}

export interface AdminFileItem {
  file_id: string
  filename: string
  size_bytes: number
  state: FileState
  share_id: string
  share_subject: string | null
  share_state: ShareState
  uploader: FileUploaderRef
  recipients_summary: string
  uploaded_at: string
  last_downloaded_at: string | null
  download_count: number
  /** Bytes still on disk + counting quota, but the parent share is
   * revoked/deleted - reclaimable to free the uploader's quota. */
  is_orphaned: boolean
}

export interface AdminFileListResponse {
  items: AdminFileItem[]
  total: number
  page: number
  page_size: number
}

/* Admin email/SMTP settings (post-Phase 10) */

export type SmtpTlsMode = 'implicit' | 'starttls' | 'none'

export interface EmailSettingsResponse {
  host: string
  port: number
  user: string
  is_password_set: boolean
  from_email: string
  from_name: string
  tls_mode: SmtpTlsMode
  /** EHLO/HELO name; '' = the server's auto-detected container FQDN is used. */
  helo_hostname: string
  is_configured: boolean
  has_db_overrides: boolean
}

export interface UpdateEmailSettingsRequest {
  host?: string
  port?: number
  user?: string
  /** null = leave alone; '' = clear; other = replace. */
  password?: string | null
  from_email?: string
  from_name?: string
  tls_mode?: SmtpTlsMode
  /** '' = clear (fall back to env/getfqdn); other = replace. */
  helo_hostname?: string
}

export interface TestEmailRequest {
  to: string
  override?: UpdateEmailSettingsRequest
}

export interface TestEmailResponse {
  ok: boolean
  error_class: string | null
  error_message: string | null
  smtp_code: number | null
  /** Human-readable next step for common SMTP failures; null when unmapped. */
  hint: string | null
}

/* Admin home-page settings (post-Phase 10) */

export interface HomePageSettingsResponse {
  enabled: boolean
}

export interface UpdateHomePageSettingsRequest {
  enabled: boolean
}

/* Admin 2FA enforcement policy (post-Phase 10) */

export interface RequiredGroupRef {
  id: number
  name: string
  is_company_inbox: boolean
}

export interface TwofaPolicyResponse {
  required_roles: string[]
  required_group_ids: number[]
  required_groups: RequiredGroupRef[]
  /** False = inheriting from REQUIRE_2FA env (no kv override saved). */
  is_kv_overridden: boolean
}

export interface UpdateTwofaPolicyRequest {
  required_roles: string[]
  required_group_ids: number[]
}

/* Admin site URL (kv override of APP_URL env, post-Phase 10) */

export interface SiteSettingsResponse {
  /** Currently effective URL (kv override → env fallback). */
  site_url: string
  /** True when the value comes from the kv override. */
  has_db_override: boolean
  /** The env value, so the UI can show "fallback to:". */
  env_app_url: string
  /** Effective IANA timezone for human-facing timestamps. Defaults
   *  to "UTC" when the kv key is unset. */
  site_timezone: string
}

export interface UpdateSiteSettingsRequest {
  /** Omitted = leave unchanged; null = clear (revert to env fallback);
   *  any other value = replace. */
  site_url?: string | null
  /** Omitted = leave unchanged; empty string = clear back to default
   *  ("UTC"); any other value must be a valid IANA name. */
  site_timezone?: string | null
}

/* Admin quarantine actions + settings (post-Phase 10) */

export interface QuarantineActionRequest {
  reason: string
}

export interface QuarantineSettingsResponse {
  notify_admins: boolean
}

export interface UpdateQuarantineSettingsRequest {
  notify_admins: boolean
}

/* v1.1.6: AV engine status + manual signature reload. */

export interface AvStatusResponse {
  available: boolean
  av_skip: boolean
  /** "ClamAV 1.5.2" or similar; null when unavailable. */
  version: string | null
  /** Signature revision number (e.g. "27543"); null when unavailable. */
  sigs_version: string | null
  /** ctime-style date string from clamd (e.g. "Fri Apr 26 10:23:45 2026").
   *  Free-text - NOT an ISO datetime, so don't run it through
   *  formatInSiteTime. */
  sigs_date: string | null
  /** Full VERSION reply for debugging when the parser splits weirdly. */
  raw: string | null
  /** Populated when available=false and av_skip=false (real error). */
  error: string | null
  /** ISO datetime of the most recent admin-triggered av_reload, or
   *  null if it's never been triggered. */
  last_reload_at: string | null
}

export interface AvReloadResponse {
  ok: boolean
  av_skip: boolean
  raw: string
}

// Admin-editable email templates (v1.25.0)
export interface EmailPlaceholderMeta {
  token: string
  label: string
  description: string
  kind: string
  required: boolean
}

export interface EmailTemplateLocale {
  code: string
  label: string
}

export interface EmailTemplateSummaryItem {
  slug: string
  group: string
  has_override: Record<string, boolean>
}

export interface EmailTemplatesListResponse {
  locales: EmailTemplateLocale[]
  groups: string[]
  items: EmailTemplateSummaryItem[]
  placeholders: Record<string, EmailPlaceholderMeta[]>
}

export interface EmailTemplateItem {
  slug: string
  group: string
  locale: string
  has_override: boolean
  subject: string
  body_html: string
  default_subject: string
  default_body: string
  placeholders: EmailPlaceholderMeta[]
}

export interface UpdateEmailTemplateRequest {
  subject: string | null
  body_html: string
}

export interface PreviewEmailTemplateRequest {
  subject: string | null
  body_html: string
}

export interface PreviewEmailTemplateResponse {
  subject: string
  text: string
  html: string
}

export interface TestSendEmailTemplateRequest {
  subject: string | null
  body_html: string
}

export interface TestSendEmailTemplateResponse {
  ok: boolean
  error_class: string | null
  error_message: string | null
  smtp_code: number | null
  hint: string | null
  sent_to: string | null
}

// Inbound mailbox / IMAP (v1.27.0)
export interface ImapSettingsResponse {
  require_known_sender?: boolean
  enabled: boolean
  use_smtp_credentials: boolean
  host: string
  port: number
  user: string
  is_password_set: boolean
  tls_mode: 'implicit' | 'starttls' | 'none'
  mailbox: string
  post_fetch_action: 'mark_read' | 'untouched' | 'move' | 'delete'
  move_folder: string
  notify_mode: 'off' | 'human' | 'all'
  last_poll_at: string | null
  last_success_at: string | null
}

export interface UpdateImapSettingsRequest {
  enabled: boolean
  use_smtp_credentials: boolean
  host: string
  port: number
  user: string
  password: string | null
  tls_mode: 'implicit' | 'starttls' | 'none'
  mailbox: string
  post_fetch_action: 'mark_read' | 'untouched' | 'move' | 'delete'
  move_folder: string
  notify_mode: 'off' | 'human' | 'all'
}

// Scheduled tasks / crons (v1.28.0)
export interface CronCounts {
  success: number
  failure: number
  running: number
}

export interface CronScheduleItem {
  name: string
  group: string
  description: string
  enabled: boolean
  kind: 'interval' | 'daily'
  interval_minutes: number
  daily_time: string
  min_interval_minutes: number
  last_run_at: string | null
  last_status: 'running' | 'success' | 'failure' | null
  last_duration_ms: number | null
  last_error: string | null
  next_run_at: string | null
  last_24h: CronCounts
  alert_on_failure: boolean
}

export interface CronListResponse {
  items: CronScheduleItem[]
  site_timezone: string
  error_alerts_enabled: boolean
}

export interface UpdateCronScheduleRequest {
  enabled: boolean
  kind: 'interval' | 'daily'
  interval_minutes: number
  daily_time: string
  alert_on_failure: boolean
}

export interface ErrorAlertSettingsResponse {
  enabled: boolean
  source_http_5xx: boolean
  source_http_4xx: boolean
  recipients_mode: 'admins' | 'custom'
  custom_recipients: string[]
  cooldown_minutes: number
  max_per_hour: number
  /** Error LOG (decoupled from the alert switches above). */
  log_enabled: boolean
  capture_4xx: boolean
  http_4xx_codes: number[]
  retention_days: number
}

export type UpdateErrorAlertSettingsRequest = ErrorAlertSettingsResponse

export interface ImapTestResponse {
  ok: boolean
  error: string | null
  hint: string | null
  folders: string[]
}

export interface ImapFetchNowResponse {
  ok: boolean
  skipped: string | null
  error: string | null
  fetched: number | null
  ingested: number | null
  mailbox: string | null
  total: number | null
}

export type InboxClass = 'normal' | 'bounce' | 'auto_reply'
export type InboxStatus = 'new' | 'read' | 'archived'

export interface InboxListItem {
  id: number
  created_at: string
  received_at: string | null
  sender_email: string
  sender_name: string | null
  sender_user_id: number | null
  subject: string
  classification: InboxClass
  status: InboxStatus
  has_attachments: boolean
}

export interface InboxListResponse {
  items: InboxListItem[]
  total: number
  page: number
  page_size: number
  unread: number
}

export interface InboxAttachmentItem {
  id: number
  filename: string
  content_type: string | null
  size_bytes: number
  av_state: 'pending' | 'clean' | 'infected'
}

export interface InboxDetail extends InboxListItem {
  to_addr: string | null
  message_id: string | null
  in_reply_to: string | null
  body_text: string | null
  body_html: string | null
  attachments: InboxAttachmentItem[]
}

export interface UpdateInboxStatusRequest {
  status: InboxStatus
}
