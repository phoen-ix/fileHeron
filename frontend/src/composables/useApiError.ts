/* Translates an axios error envelope into a user-readable message via i18n.
 * Falls back gracefully through: known code → server error text → generic
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

  return { describe }
}
