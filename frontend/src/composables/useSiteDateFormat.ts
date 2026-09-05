/* Locale-bound wrappers around the site-timezone formatters in
 * `utils/datetime`. Every view was redeclaring a one-line
 * `formatDate(iso) => formatInSiteTime(iso, locale.value)`; this binds the
 * active i18n locale once so callers get `formatDate` / `formatDateOnly`
 * without repeating the `locale.value` plumbing. `opts` still passes through
 * for the few surfaces that need seconds or a date-only cell.
 */
import { useI18n } from 'vue-i18n'

import {
  formatDateInSiteTime,
  formatExpiryInSiteTime,
  formatInSiteTime,
} from '@/utils/datetime'

export function useSiteDateFormat() {
  const { locale, t } = useI18n()
  return {
    /** Date + time (+ site-tz token) in the admin-set timezone. */
    formatDate: (iso: string | null | undefined, opts?: Intl.DateTimeFormatOptions) =>
      formatInSiteTime(iso, locale.value, opts),
    /** Date-only variant for table cells that don't need a clock. */
    formatDateOnly: (iso: string | null | undefined, opts?: Intl.DateTimeFormatOptions) =>
      formatDateInSiteTime(iso, locale.value, opts),
    /** Share expiry: `null` renders the localized "Never" instead of a dash.
     *  Three views carried the same one-line wrapper. */
    formatExpiry: (iso: string | null | undefined, opts?: Intl.DateTimeFormatOptions) =>
      formatExpiryInSiteTime(iso, locale.value, t('expiry.never_label'), opts),
  }
}
