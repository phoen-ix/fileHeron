import type { SecretLimitsResponse } from '@/types/api'

/** What the compose pages fall back to before `/me` has said - the backend's
 *  shipped defaults. One copy: the secret and request compose pages each
 *  hard-coded it (audit 2026-11-08); the server enforces its own values either
 *  way. */
export const DEFAULT_SECRET_LIMITS: SecretLimitsResponse = {
  max_views: 100,
  max_expiry_days: 90,
  max_lifetime_days: 90,
  passphrase_failure_mode: 'lock',
  passphrase_max_failures: 10,
}
