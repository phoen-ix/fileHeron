/* TypeScript shapes mirroring the FastAPI Pydantic schemas. Hand-written
 * (not generated) — small surface, one place to update when the API shape
 * shifts. */

export type Locale = 'en' | 'de'
export type UserRole = 'admin' | 'employee' | 'client'

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
}

export interface ShareDefaultsResponse {
  notify_recipients_default: boolean
}

export interface UpdateShareDefaultsRequest {
  notify_recipients_default: boolean
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
export type ShareState = 'active' | 'expired' | 'revoked' | 'deleted' | 'failed'
export type FileState =
  | 'pending'
  | 'ready_unscanned'
  | 'clean'
  | 'infected'
  | 'failed'
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
}

export interface GroupRecipientRef {
  id: number
  name: string
  is_company_inbox: boolean
}

export interface InlinePublicLinkResult {
  id: string
  url: string
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
   *  Mutually exclusive with expires_at — sending both is a 400. */
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
  download_limit: number | null
  downloads_remaining: number | null
  notify_on_download: boolean
  has_password: boolean
  locked_until: string | null
  revoked_at: string | null
  created_at: string
}

export interface CreatePublicLinkResponse extends PublicLinkResponse {
  /** Always set on create — narrows the parent's nullable url. */
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
  files: PublicShareFile[]
}

/* Notifications + preferences (Phase 6a/6b) */

export type NotificationCategory =
  | 'share_created'
  | 'share_expiring'
  | 'public_link_downloaded'
  | 'account_created'
  | 'password_reset'
  | 'twofa_required'
  | 'login_alert'
  | 'file_quarantined'
  | 'ops_alert'
  | 'release_available'

export type NotificationChannel = 'off' | 'email' | 'in_app' | 'both'

export interface PreferenceItem {
  category: NotificationCategory
  channel: NotificationChannel
}

export interface PreferencesResponse {
  items: PreferenceItem[]
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
  /** Live Redis quota counter — kept honest by the hourly
   * `quota_reconcile` cron. Useful for spotting who's eating disk on
   * the /admin/users list without drilling into file history. */
  storage_used_bytes: number
  created_at: string
  last_login_at: string | null
  has_2fa: boolean
}

export interface AdminUserListResponse {
  items: AdminUserItem[]
  total: number
  page: number
  page_size: number
}

/* Admin pending-invites views (post-Phase 10). */

/** v1.1.5: 'revoked' dropped — admin delete is now a hard delete. */
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

export interface EraseUserResponse {
  user_id: number
  deleted_files: number
  deleted_bytes: number
  erased_at: string
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
  owner_user_id?: number
}

/* Admin API tokens (post-Phase 10) */

export type TokenPolicyMode =
  | 'everyone'
  | 'employees_admins'
  | 'admins_only'
  | 'disabled'

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
   * revoked/deleted — reclaimable to free the uploader's quota. */
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
   *  Free-text — NOT an ISO datetime, so don't run it through
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
