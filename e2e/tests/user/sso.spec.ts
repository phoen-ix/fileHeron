import type { Page } from '@playwright/test'

import { apiFetch, enroll2FA, totpCode } from '../../helpers'
import { admin, expect, newUser, ok, signIn, test, uid } from './lib'

/* Single sign-on against the stand-in OIDC provider the e2e stack runs
 * (e2e/oidc/idp.py): the sign-in button and the round trip, the first SSO
 * sign-in linking the account with the same verified email, an identity with
 * no account, the second-factor page an enrolled account stops at, and
 * connecting and disconnecting SSO from the account page. */

let provider = { id: '', name: '' }

test.beforeAll(async () => {
  const name = `E2E IdP ${uid()}`
  const created = await ok<{ id: string }>(
    apiFetch(await admin(), '/api/admin/settings/sso/providers', {
      method: 'POST',
      body: JSON.stringify({
        name,
        preset: 'custom',
        issuer_url: 'http://oidc:9000',
        client_id: 'fileheron-e2e',
        client_secret: 'e2e-idp-not-a-secret',
        enabled: true,
      }),
    }),
    'create SSO provider',
  )
  provider = { id: created.id, name }
})

// Switched off rather than deleted: the accounts it linked keep it (a linked
// provider cannot be deleted), and the sign-in page shows only enabled ones.
test.afterAll(async () => {
  await ok(
    apiFetch(await admin(), `/api/admin/settings/sso/providers/${provider.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled: false }),
    }),
    'disable SSO provider',
  )
})

/** From the sign-in page through the IdP's form, as `email`. */
async function ssoSignIn(page: Page, email: string): Promise<void> {
  await page.goto('/login')
  await page.getByRole('button', { name: `Sign in with ${provider.name}` }).click()
  await expect(page.getByRole('heading', { name: 'E2E identity provider' })).toBeVisible()
  await page.getByLabel('Email').fill(email)
  await page.getByRole('button', { name: 'Sign in at the IdP' }).click()
}

/** Back from the IdP the SPA renews its session from the new cookie; leaving
 * the page before that answer arrives would replay a spent refresh token. */
async function signedIn(page: Page): Promise<void> {
  await expect(page.getByRole('button', { name: 'Menu' })).toBeVisible()
}

test('the first SSO sign-in links the account with that verified email', async ({ page }) => {
  const p = await newUser('client', 'sso')
  await ssoSignIn(page, p.email)
  await signedIn(page)
  await page.goto('/account')
  await expect(page.getByRole('heading', { level: 1, name: p.name })).toBeVisible()
  await expect(page.locator('.oidc-row', { hasText: provider.name }).getByText(/^Connected/)).toBeVisible()

  // Signed out, the same identity signs straight in again.
  await page.context().clearCookies()
  await ssoSignIn(page, p.email)
  await signedIn(page)
})

test('an identity with no account is sent back with the reason', async ({ page }) => {
  await ssoSignIn(page, `stranger-${uid()}@e2e.example`)
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByRole('alert')).toHaveText(
    'No fileHeron account exists for this identity. Ask an admin to invite you first.',
  )
})

test('an account with two-factor stops at the second-factor page after SSO', async ({ page }) => {
  const p = await newUser('employee', 'sso2fa')
  const secret = await enroll2FA(p.email, p.password)
  await ssoSignIn(page, p.email)
  await expect(page).toHaveURL(/\/login\/2fa$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Two-factor authentication' })).toBeVisible()
  await page.getByLabel('Authentication code').fill('000000')
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await page.getByLabel('Authentication code').fill(totpCode(secret))
  await page.getByRole('button', { name: 'Continue' }).click()
  await signedIn(page)
})

test('SSO is connected and disconnected from the account page', async ({ page }) => {
  const p = await newUser('client', 'ssoconnect')
  await signIn(page, p)
  await page.goto('/account')
  const row = page.locator('.oidc-row', { hasText: provider.name })
  await row.getByRole('button', { name: 'Connect' }).click()
  await page.getByLabel('Email').fill(p.email)
  await page.getByRole('button', { name: 'Sign in at the IdP' }).click()
  await expect(page).toHaveURL(/\/account/)
  await expect(page.getByText('SSO provider connected.')).toBeVisible()
  await expect(row.getByText(/^Connected/)).toBeVisible()

  await row.getByRole('button', { name: 'Disconnect' }).click()
  await page.getByRole('dialog').getByRole('button').last().click()
  await expect(page.getByText('SSO provider disconnected.')).toBeVisible()
  await expect(row.getByRole('button', { name: 'Connect' })).toBeVisible()
})
