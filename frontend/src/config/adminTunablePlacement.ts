/* Which admin PAGE renders each registry tunable (services/settings_registry.py).
 *
 * The backend keeps ONE writer for every registry knob - `PUT
 * /api/admin/settings/advanced` - because a second raw writer is how the scan
 * guard's IPv6 prefix once orphaned live network blocks (see
 * `_MANAGED_ELSEWHERE_GROUPS` in routers/admin/settings/advanced.py). What
 * moved is the SURFACE: a tunable is shown on the page of its task, through
 * `components/admin/TunableFields.vue`, which filters the endpoint's items by
 * this map. "Advanced" is therefore no longer a grab-bag; what remains there is
 * data retention and storage, and the page is named for that.
 *
 * Every key renders on exactly one page. `null` means the page owns the key
 * through its own form and PUT (the Errors & alerts page writes the alert
 * throttle and the log retention itself, and its PUT resets the error-log
 * process cache) - TunableFields never renders those, so they are not shown
 * twice. Pinned by `tests/config/adminTunablePlacement.test.ts` (every route
 * exists) and `backend/tests/test_admin_search_index_pin.py` (the search
 * index points each tunable at the page that renders it). */

export const ADVANCED_ROUTE = 'admin-settings-advanced'

/** Registry `group` → route name of the page that renders it. */
export const GROUP_PLACEMENT: Readonly<Record<string, string>> = {
  sessions: 'admin-settings-sessions',
  rate_limits: 'admin-settings-sign-in',
  security: 'admin-settings-sign-in',
  uploads: 'admin-settings-transfers',
  downloads: 'admin-settings-transfers',
  updates: 'admin-system',
  branding: 'admin-settings-branding',
  anomaly: 'admin-settings-anomaly',
  error_alert: 'admin-settings-error-alerts',
  retention: ADVANCED_ROUTE,
  storage: ADVANCED_ROUTE,
}

/** Per-key exceptions to the group placement. */
export const KEY_PLACEMENT: Readonly<Record<string, string | null>> = {
  // The public-link brute-force triple sits with the public-link policy, not
  // with the sign-in limits its registry group lumps it into.
  'public_link.password_rate_limit': 'admin-settings-public-links',
  'public_link.password_window_sec': 'admin-settings-public-links',
  'public_link.lockout_sec': 'admin-settings-public-links',
  // Rendered by the Errors & alerts page's own form ("Anti-flood" and "Keep
  // log entries for"), whose PUT is the writer that resets the error-log cache.
  'error_alert.cooldown_minutes': null,
  'error_alert.max_per_hour': null,
  'error_log.retention_days': null,
}

/** The page that renders `key`, or null when a page's own form owns it. An
 *  unknown group lands on the Advanced page so a new tunable is never
 *  invisible. */
export function placementFor(key: string, group: string): string | null {
  if (key in KEY_PLACEMENT) return KEY_PLACEMENT[key]
  return GROUP_PLACEMENT[group] ?? ADVANCED_ROUTE
}
