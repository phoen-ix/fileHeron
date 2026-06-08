import { expect, test } from '@playwright/test'
import { authenticator } from 'otplib'

import { ADMIN, apiLogin, createUser, enroll2FA, freshPassword } from '../helpers'

/* Journey 2: a user with TOTP enabled must supply a code at login. Uses a fresh
 * dedicated account (enabling 2FA on a shared one would break other journeys'
 * plain logins). */

test('login requires + accepts a TOTP code once 2FA is enabled', async ({ page }) => {
  const admin = await apiLogin(ADMIN.email, ADMIN.password)
  const email = `totp+${Date.now()}@e2e.local`
  const password = freshPassword()
  await createUser(admin, { email, password, role: 'client', displayName: 'Totp User' })
  const secret = await enroll2FA(email, password)

  await page.goto('/login')
  await page.fill('#login-email', email)
  await page.fill('#login-password', password)
  await page.click('button[type=submit]')

  // Password alone -> the backend answers TOTP_REQUIRED; the SPA reveals the
  // code field and keeps us on /login.
  await expect(page.locator('#login-code')).toBeVisible()
  await expect(page).toHaveURL(/\/login(\?|$)/)

  await page.fill('#login-code', authenticator.generate(secret))
  await page.click('button[type=submit]')
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
})
