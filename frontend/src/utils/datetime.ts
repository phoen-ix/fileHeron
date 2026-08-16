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
import dayjs from 'dayjs'
import timezone from 'dayjs/plugin/timezone'
import utc from 'dayjs/plugin/utc'

import { useSiteStore } from '@/stores/site'

// utc + timezone ship with dayjs core (no extra dependency). timezone
// depends on utc, so register utc first.
dayjs.extend(utc)
dayjs.extend(timezone)

const TZ_DESIGNATOR_RE = /[zZ]|[+-]\d{2}:?\d{2}$/

function siteTz(): string {
  return useSiteStore().timezone || 'UTC'
}

/** Parse a backend ISO datetime as UTC, appending `Z` when no
 *  timezone designator is present. Returns an invalid `Date` if
 *  the input is malformed (mirroring `new Date(...)` semantics). */
export function parseServerDate(iso: string): Date {
  const fixed = TZ_DESIGNATOR_RE.test(iso) ? iso : iso + 'Z'
  return new Date(fixed)
}

/** Format a backend ISO datetime in the admin-set site timezone using
 *  the caller's locale. `opts` overrides any field; defaults to
 *  year + short-month + day + hour + minute. Returns `'-'` when the
 *  input is null or unparseable so callers can drop it into a table
 *  cell without further nil-checks.
 *
 *  Locale is the user's i18n locale (de | en) - separate concept from
 *  the timezone (de-AT user could be in Asia/Tokyo). */
export function formatInSiteTime(
  iso: string | null | undefined,
  locale: string,
  opts?: Intl.DateTimeFormatOptions,
): string {
  if (!iso) return '-'
  const date = parseServerDate(iso)
  if (Number.isNaN(date.getTime())) return '-'
  const site = useSiteStore()
  return new Intl.DateTimeFormat(locale === 'de' ? 'de-AT' : 'en-US', {
    timeZone: site.timezone || 'UTC',
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    // 24-hour + a DST-aware zone token ("GMT+2"/"CEST" in summer, "UTC" if
    // the site zone is UTC) so timestamps are unambiguous - a 12-hour
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

/** Wall-clock "YYYY-MM-DDTHH:mm:ss" string in the admin-set SITE timezone
 *  for the instant `now + ms`. Used by the expiry picker so its defaults +
 *  presets are expressed in the same timezone the app *displays* expiry in
 *  (formatInSiteTime), instead of the viewer's browser timezone. Without
 *  this, a viewer whose browser tz ≠ the site tz picks "7 days" and the
 *  stored/displayed expiry lands off by the offset between the two. */
export function siteNowPlusIso(ms: number): string {
  return dayjs(Date.now() + ms)
    .tz(siteTz())
    .format('YYYY-MM-DDTHH:mm:ss')
}

/** Inverse of the picker convention: interpret a naive "YYYY-MM-DDTHH:mm:ss"
 *  wall-clock string as a time in the SITE timezone and return the matching
 *  UTC instant as an ISO string (with `Z`). The backend strips the `Z` and
 *  stores naive UTC; `formatInSiteTime` then renders it back in the site tz,
 *  so the round-trip preserves the exact wall-clock the user picked. */
export function siteLocalIsoToUtcIso(siteLocal: string): string {
  return dayjs.tz(siteLocal, siteTz()).utc().toISOString()
}

/** Parse a naive site-tz wall-clock string to an epoch-ms instant (for
 *  "expires in N days" style relative hints in the picker). */
export function siteLocalIsoToEpochMs(siteLocal: string): number {
  return dayjs.tz(siteLocal, siteTz()).valueOf()
}

/** Share expiry display: null means "Never" (v1.1.4 - admin-set
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

/** 90 days out, in the site-local `YYYY-MM-DDTHH:mm` an ExpiryPicker binds to.
 *
 *  Shared so the two token forms cannot drift: the self-service panel defaulted
 *  to 90 days + limited scopes while the ADMIN form — the one that mints a
 *  credential for somebody else, and the one a stolen admin session reaches for
 *  — still defaulted to never-expiring and unrestricted. */
export function defaultTokenExpiryLocal(): string {
  const d = new Date()
  d.setDate(d.getDate() + 90)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
