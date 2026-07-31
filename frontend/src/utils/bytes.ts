import { i18n } from '@/i18n'

/**
 * Human-readable byte size - the single source of truth for every view.
 *
 * Two things were wrong with the previous state (audit 2026-07-30,
 * fe-i18n-a11y-13):
 *
 * 1. FileRow.vue and UploadFileRow.vue each carried their OWN copy with a
 *    different precision rule (`size < 10 ? 2 : 1` decimals against this
 *    file's `>= 100 || bytes ? 0 : 1`), so the same file was "1.46 MB" in the
 *    upload list and "1.5 MB" in the file list of the same share. Both copies
 *    are gone; they import this.
 * 2. The number was formatted with `toFixed`, which always renders a `.`
 *    decimal separator - so a German page read "1.5 MB" where every other
 *    number on it read "1,5". The separator now follows the active locale.
 *
 * Renders e.g. "0 B", "512 B", "1.5 KB" / "1,5 KB", "240 MB", "3.2 GB".
 */
export function formatBytes(n: number | null | undefined): string {
  if (!n || n < 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  // Whole numbers for bytes and for large values (>= 100); one decimal
  // otherwise. Same rule as before - only the separator is locale-aware.
  const digits = v >= 100 || i === 0 ? 0 : 1
  const locale = i18n.global.locale.value ?? 'en'
  const rendered = new Intl.NumberFormat(locale, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(v)
  return `${rendered} ${units[i]}`
}
