/* Translates an axios error envelope into a user-readable message via i18n.
 * Falls back gracefully through: known code -> server error text -> generic
 * message. */
import { useI18n } from 'vue-i18n'

import { asEnvelope } from '@/api/client'

export function useApiError() {
  const { t, te } = useI18n()

  function describe(err: unknown): string {
    const env = asEnvelope(err)
    if (env) {
      const key = `errors.${env.code}`
      if (te(key)) return t(key)
      return env.error ?? t('errors.generic')
    }
    return t('errors.generic')
  }

  /**
   * `describe` for a request made with `responseType: 'blob'`.
   *
   * A blob-typed request gets a BLOB error body too, so `asEnvelope` sees an
   * object with no `code` and every failure - a 403 on the CSV export, a 413,
   * a 500 - rendered as the same generic "something went wrong". The admin
   * downloads (audit CSV, mail CSV, error CSV, config backup, quarantine file)
   * are exactly where the specific reason matters (audit 2026-07-30,
   * fe-correct-12).
   *
   * Reading a Blob is async, hence the separate function rather than a smarter
   * `describe`.
   */
  async function describeBlob(err: unknown): Promise<string> {
    const body = (err as { response?: { data?: unknown } })?.response?.data
    if (body instanceof Blob) {
      try {
        const text = await body.text()
        const parsed = JSON.parse(text) as { code?: string; error?: string }
        if (typeof parsed?.code === 'string') {
          const key = `errors.${parsed.code}`
          if (te(key)) return t(key)
          return parsed.error ?? t('errors.generic')
        }
      } catch {
        /* not JSON, or unreadable - fall through to the generic path */
      }
    }
    return describe(err)
  }

  return { describe, describeBlob }
}
