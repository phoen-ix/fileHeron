import { expect, test } from '@playwright/test'

import { USER } from '../helpers'

/* Journey 6: silent refresh. After login the httpOnly refresh cookie is set but
 * the access token lives only in memory. A fresh page (same context: cookie
 * present, no in-memory token) deep-linking to an authed route must
 * silent-refresh via /api/auth/refresh and render - not bounce to /login. */

test('a fresh page boot silently refreshes from the cookie and stays authed', async ({
  page,
  context,
}) => {
  await page.goto('/login')
  await page.fill('#login-email', USER.email)
  await page.fill('#login-password', USER.password)
  await page.click('button[type=submit]')
  await expect(page).not.toHaveURL(/\/login(\?|$)/)

  const fresh = await context.newPage()
  await fresh.goto('/account')
  await expect(fresh).toHaveURL(/\/account/)
  await expect(fresh).not.toHaveURL(/\/login(\?|$)/)
  await fresh.close()
})
