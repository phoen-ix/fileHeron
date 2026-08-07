import { expect, test } from '@playwright/test'

import { ADMIN, apiFetch, apiLogin } from '../helpers'

/* Journey 8: a least-privilege API token is refused outside its scopes.
 * Pure API - the scope enforcement is the wiring under test. */

test('a shares:read token is refused on a shares:create call (403)', async () => {
  const admin = await apiLogin(ADMIN.email, ADMIN.password)

  const created = await apiFetch(admin, '/api/account/api-tokens', {
    method: 'POST',
    // `password` re-confirms the caller: a token outlives the session that
    // minted it and is not revoked by a password reset, so creating one is
    // gated the same way changing a password is.
    body: JSON.stringify({
      name: 'e2e-readonly',
      scopes: ['shares:read'],
      password: ADMIN.password,
    }),
  })
  expect(created.status).toBe(201)
  const token = (await created.json()).plaintext_token as string

  // Out of scope: creating a share needs shares:create -> 403 INSUFFICIENT_SCOPE.
  const denied = await apiFetch(token, '/api/shares', {
    method: 'POST',
    body: JSON.stringify({ recipients: { user_ids: [], group_ids: [] } }),
  })
  expect(denied.status).toBe(403)
  expect((await denied.json()).code).toBe('INSUFFICIENT_SCOPE')

  // In scope: reading shares is allowed (not a 401/403).
  const allowed = await apiFetch(token, '/api/shares?box=outbox')
  expect(allowed.status).toBe(200)
})
