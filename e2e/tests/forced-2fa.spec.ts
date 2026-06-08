import { expect, test } from '@playwright/test'

import { ADMIN, apiFetch, apiLogin, createUser, freshPassword } from '../helpers'

/* Journey 7: when the active 2FA policy applies to a user who hasn't enrolled,
 * login drops them into the 2FA wizard. We require 2FA for the EMPLOYEE role +
 * use a fresh employee, so the seeded client/admin logins (other journeys)
 * stay unaffected - no policy reset needed. */

test('a 2FA-required user without TOTP is forced to /account/2fa', async ({ page }) => {
  const admin = await apiLogin(ADMIN.email, ADMIN.password)

  const set = await apiFetch(admin, '/api/admin/settings/twofa', {
    method: 'PUT',
    body: JSON.stringify({ required_roles: ['employee'], required_group_ids: [] }),
  })
  expect(set.ok).toBeTruthy()

  const email = `emp+${Date.now()}@e2e.local`
  const password = freshPassword()
  await createUser(admin, { email, password, role: 'employee', displayName: 'Emp' })

  await page.goto('/login')
  await page.fill('#login-email', email)
  await page.fill('#login-password', password)
  await page.click('button[type=submit]')

  // Authenticated but policy-bound + un-enrolled -> guard sends to the wizard.
  await expect(page).toHaveURL(/\/account\/2fa/)
})
