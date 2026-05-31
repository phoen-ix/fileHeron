/* Centralized timestamp formatting for backend-emitted ISO strings.
 *
 * The backend stores timestamps as naive UTC (no `Z`, no offset) per
 * CLAUDE.md convention. ECMAScript parses a bare "YYYY-MM-DDTHH:MM:SS"
 * as local time, which is wrong by the viewer's UTC offset. The first
 * helper fixes that; the second formats the result in the admin-set
 * site timezone (Pinia store, hydrated at app bootstrap from
 * /api/config-public).
 *
 * All admin / share / public surfaces should call `formatInSiteTime`
 * for display so a single admin setting controls the whole UI.
 */
import { useSiteStore } from '@/stores/site'

const TZ_DESIGNATOR_RE = /[zZ]|[+-]\d{2}:?\d{2}$/

/** Parse a backend ISO datetime as UTC, appending `Z` when no
 *  timezone designator is present. Returns an invalid `Date` if
 *  the input is malformed (mirroring `new Date(...)` semantics). */
export function parseServerDate(iso: string): Date {
  const fixed = TZ_DESIGNATOR_RE.test(iso) ? iso : iso + 'Z'
  return new Date(fixed)
}

/** Format a backend ISO datetime in the admin-set site timezone using
 *  the caller's locale. `opts` overrides any field; defaults to
 *  year + short-month + day + hour + minute. Returns `'—'` when the
 *  input is null or unparseable so callers can drop it into a table
 *  cell without further nil-checks.
 *
 *  Locale is the user's i18n locale (de | en) — separate concept from
 *  the timezone (de-AT user could be in Asia/Tokyo). */
export function formatInSiteTime(
  iso: string | null | undefined,
  locale: string,
  opts?: Intl.DateTimeFormatOptions,
): string {
  if (!iso) return '—'
  const date = parseServerDate(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const site = useSiteStore()
  return new Intl.DateTimeFormat(locale === 'de' ? 'de-AT' : 'en-US', {
    timeZone: site.timezone || 'UTC',
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    // 24-hour + a DST-aware zone token ("GMT+2"/"CEST" in summer, "UTC" if
    // the site zone is UTC) so timestamps are unambiguous — a 12-hour
    // "1:00 PM" with no zone reads like it might be UTC when it's already
    // the admin-set site timezone.
    hour12: false,
    timeZoneName: 'short',
    ...opts,
  }).format(date)
}

/** Date-only variant for table cells that don't need a clock. */
export function formatDateInSiteTime(
  iso: string | null | undefined,
  locale: string,
  opts?: Intl.DateTimeFormatOptions,
): string {
  return formatInSiteTime(iso, locale, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: undefined,
    minute: undefined,
    // No clock → no dangling zone token on a date-only cell.
    timeZoneName: undefined,
    ...opts,
  })
}

/** Share expiry display: null means "Never" (v1.1.4 — admin-set
 *  no-expiry shares). Renders the localized "Never" label instead of
 *  the em-dash fallback used by `formatInSiteTime`. For dated rows,
 *  delegates to formatInSiteTime so the same site-tz + locale rules
 *  apply. `neverLabel` lets the caller pass the localized string
 *  (i18n `t()` is component-scoped, not module-scoped). */
export function formatExpiryInSiteTime(
  iso: string | null | undefined,
  locale: string,
  neverLabel: string,
  opts?: Intl.DateTimeFormatOptions,
): string {
  if (iso === null) return neverLabel
  return formatInSiteTime(iso, locale, opts)
}
