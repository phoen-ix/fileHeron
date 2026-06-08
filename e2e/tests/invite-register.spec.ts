import { expect, test } from '@playwright/test'

import { ADMIN, apiFetch, apiLogin, freshPassword, tokenFromStdout } from '../helpers'

/* Journey 5: admin invites a user; the invitee follows the one-time link (read
 * from backend stdout, since the mail log masks the token) and registers. */

test('admin invite -> stdout token -> register lands in the app', async ({ page }) => {
  const admin = await apiLogin(ADMIN.email, ADMIN.password)
  const email = `invitee+${Date.now()}@e2e.local`

  const inv = await apiFetch(admin, '/api/account/invite', {
    method: 'POST',
    body: JSON.stringify({ email, target_role: 'client', display_name_hint: 'Invitee' }),
  })
  expect(inv.ok).toBeTruthy()

  const token = tokenFromStdout('register')
  await page.goto(`/register/${token}`)
  await page.fill('#reg-name', 'Invitee E2E')
  await page.fill('#reg-pw', freshPassword())
  await page.click('button[type=submit]')

  // register-from-invite logs the new user straight in -> off the register page.
  await expect(page).not.toHaveURL(/\/register\//)
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
})
