import { expect, test } from '@playwright/test'

import { USER } from '../helpers'

/* Journey 1: a normal user logs in and lands in the app; bad credentials stay
 * on /login with an error. Exercises the real login form -> token -> router
 * guard redirect that the backend tests can't see. */

test('login with valid credentials lands in the app', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#login-email', USER.email)
  await page.fill('#login-password', USER.password)
  await page.click('button[type=submit]')

  // The guard redirects off /login to the effective landing page.
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
  // A reload keeps us authed (silent-refresh bootstrap), i.e. not bounced back.
  await page.reload()
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
})

test('login with a wrong password stays on /login', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#login-email', USER.email)
  await page.fill('#login-password', 'definitely-not-the-password')
  await page.click('button[type=submit]')

  await expect(page).toHaveURL(/\/login(\?|$)/)
})
