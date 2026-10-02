/* Where each settings change on the Overview's "Recently changed" panel is
 * made. A settings-change audit row is `target_type: "settings"` with a
 * `target_id` naming the area ("smtp", "legal", "twofa_policy", ...); this maps
 * that id - or, for a few events, the event itself - to the admin page that
 * owns the setting. `backend/tests/test_settings_change_links_pin.py` reads this
 * file and fails when a backend settings audit writes a target_id missing here,
 * or a key here is written by nothing. */
import { ADMIN_SEARCH_INDEX } from '@/config/adminSearchIndex'
import type { AdminAuditRow } from '@/types/api'

export const SETTINGS_TARGET_ROUTES: Readonly<Record<string, string>> = {
  api_token_policy: 'admin-settings-api-tokens',
  auto_update: 'admin-system',
  updates: 'admin-system',
  branding: 'admin-settings-branding',
  branding_logo: 'admin-settings-branding',
  legal: 'admin-settings-legal',
  email_change: 'admin-settings-email-change',
  error_alerts: 'admin-settings-error-alerts',
  file_preview: 'admin-settings-transfers',
  share_defaults: 'admin-settings-transfers',
  home_page: 'admin-settings-general',
  motd: 'admin-settings-general',
  site_url: 'admin-settings-general',
  site_timezone: 'admin-settings-general',
  imap: 'admin-settings-imap',
  maintenance: 'admin-settings-maintenance',
  public_link_policy: 'admin-settings-public-links',
  quarantine: 'admin-settings-quarantine',
  scan_guard: 'admin-settings-scan-guard',
  secret_policy: 'admin-settings-secrets',
  share_approval: 'admin-settings-share-approval',
  smtp: 'admin-settings-email',
  twofa_policy: 'admin-settings-twofa',
}

/** Events whose page is not decided by their target id. */
export const EVENT_ROUTES: Readonly<Record<string, string>> = {
  // The allowlist is edited on the blocks page, though it is filed under the
  // scan guard's target.
  ip_allowlisted: 'admin-ip-blocks',
  ip_allowlist_removed: 'admin-ip-blocks',
  email_template_changed: 'admin-settings-email-templates',
  email_template_reset: 'admin-settings-email-templates',
  cron_schedule_changed: 'admin-scheduled-tasks',
}

export interface SettingsChangeLink {
  routeName: string
  hash?: string
}

/** The page a change was made on. A generic `settings_changed` row (the
 * tunables form) names its keys; the first one's `#tunable-<key>` anchor is
 * looked up in the search index, which already knows which page renders each
 * tunable. Unknown rows fall back to the audit log. */
export function settingsChangeLink(row: AdminAuditRow): SettingsChangeLink {
  const byEvent = EVENT_ROUTES[row.event_type]
  if (byEvent) return { routeName: byEvent }
  if (row.target_id && SETTINGS_TARGET_ROUTES[row.target_id]) {
    return { routeName: SETTINGS_TARGET_ROUTES[row.target_id] }
  }
  const keys = (row.extra?.keys as string[] | undefined) ?? []
  for (const key of keys) {
    const entry = ADMIN_SEARCH_INDEX.find((e) => e.hash === `#tunable-${key}`)
    if (entry) return { routeName: entry.routeName, hash: entry.hash }
  }
  if (row.event_type === 'settings_changed') return { routeName: 'admin-settings-advanced' }
  return { routeName: 'admin-audit' }
}

/** What changed, in a few words: the keys a tunables save named, or the
 * template a template event names. Never a value. */
export function settingsChangeDetail(row: AdminAuditRow): string | null {
  const keys = row.extra?.keys
  if (Array.isArray(keys) && keys.length) return (keys as string[]).join(', ')
  if (row.event_type.startsWith('email_template_') && row.target_id) return row.target_id
  return null
}
