import type { Page } from '@playwright/test'

import { BASE } from '../../helpers'
import {
  expect,
  mailedToken,
  newUser,
  restoreSettings,
  session,
  setSettings,
  signIn,
  test,
  uid,
} from './lib'

/* The account page, section by section: profile (name, language, landing
 * page), password, email change (confirm and cancel), sessions, API tokens,
 * notification preferences and passkeys. */

const confirmDialog = (page: Page) => page.getByRole('dialog').getByRole('button').last()

test('profile: display name, language and landing page', async ({ page }) => {
  const p = await newUser('employee', 'profile')
  await signIn(page, p)
  await page.goto('/account')

  const name = page.getByLabel('Display name')
  await name.fill('Renamed Person')
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(name).toHaveValue(p.name)
  await name.fill('Renamed Person')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Display name saved.')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1, name: 'Renamed Person' })).toBeVisible()

  await page.locator('#profile').getByRole('button', { name: 'Deutsch' }).click()
  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await expect(page.getByRole('heading', { level: 2, name: 'Profil' })).toBeVisible()
  await page.locator('#profile').getByRole('button', { name: 'English' }).click()
  await expect(page.getByRole('heading', { level: 2, name: 'Profile' })).toBeVisible()

  await page.getByLabel('Default landing page').selectOption({ label: 'Received' })
  await expect(page.getByText('Default landing page saved.')).toBeVisible()
  await page.context().clearCookies()
  await page.goto('/login')
  await page.getByLabel('Email', { exact: true }).fill(p.email)
  await page.getByLabel('Password', { exact: true }).fill(p.password)
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page).toHaveURL(/\/inbox$/)
})

test('password: a wrong current password is refused; a change keeps only this device', async ({
  page,
  browser,
}) => {
  const p = await newUser('client', 'passwd')
  const other = await session(browser, p)
  await signIn(page, p)
  await page.goto('/account')
  const fresh = `E2e!changed${uid()}Qz`

  await page.locator('#acc-cur').fill('not-my-password-1')
  await page.locator('#acc-new').fill(fresh)
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.locator('#password').getByRole('alert')).toBeVisible()

  await page.locator('#acc-cur').fill(p.password)
  await page.locator('#acc-new').fill(fresh)
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.getByText('Password changed. Other sessions revoked.')).toBeVisible()

  // This tab carries on; the other device is signed out at its next load.
  await page.reload()
  await expect(page).toHaveURL(/\/account$/)
  await other.goto('/account')
  await expect(other).toHaveURL(/\/login/)
  await signIn(other, { email: p.email, password: fresh })
  await other.context().close()
})

test.describe.serial('email change', () => {
  let before: Record<string, unknown>
  test.beforeAll(async () => {
    before = await setSettings('/api/admin/settings/email-change', {
      self_service: true,
      verification_mode: 'verify_new',
    })
  })
  test.afterAll(async () => restoreSettings('/api/admin/settings/email-change', before))

  test('the link mailed to the new address confirms the change', async ({ page }) => {
    const p = await newUser('client', 'emailchg')
    const next = `moved-${uid()}@e2e.example`
    await signIn(page, p)
    await page.goto('/account')
    await page.getByLabel('New email address').fill(next)
    await page.locator('#email').getByLabel('Current password').fill(p.password)
    await page.getByRole('button', { name: 'Change email' }).click()
    await expect(page.getByTestId('email-pending')).toContainText(next)

    await page.goto(`/confirm-email-change/${await mailedToken('confirm-email-change', next)}`)
    await expect(page.getByRole('heading', { name: 'Email changed.' })).toBeVisible()
    await page.context().clearCookies()
    await signIn(page, { email: next, password: p.password })
    await page.goto('/account')
    await expect(page.getByText(next).first()).toBeVisible()
  })

  test('a pending change is cancelled from the account page or the old address', async ({
    page,
  }) => {
    const p = await newUser('client', 'emailcancel')
    await signIn(page, p)
    await page.goto('/account')
    for (const how of ['page', 'link'] as const) {
      const next = `cancel-${how}-${uid()}@e2e.example`
      await page.getByLabel('New email address').fill(next)
      await page.locator('#email').getByLabel('Current password').fill(p.password)
      await page.getByRole('button', { name: 'Change email' }).click()
      await expect(page.getByTestId('email-pending')).toContainText(next)
      if (how === 'page') {
        await page.getByTestId('email-pending-cancel').click()
        await confirmDialog(page).click()
        await expect(page.getByText('Pending email change cancelled.')).toBeVisible()
        await expect(page.getByTestId('email-pending')).toHaveCount(0)
      } else {
        const token = await mailedToken('cancel-email-change', p.email)
        await page.goto(`/cancel-email-change/${token}`)
        await expect(page.getByRole('heading', { name: 'Change cancelled.' })).toBeVisible()
        // The confirm link mailed to the new address is dead now.
        await page.goto(`/confirm-email-change/${await mailedToken('confirm-email-change', next)}`)
        await expect(page.getByRole('heading', { name: 'Change not confirmed.' })).toBeVisible()
      }
    }
  })
})

test('sessions: revoke one, then sign out all others', async ({ page, browser }) => {
  const p = await newUser('client', 'sessions')
  const a = await session(browser, p)
  const b = await session(browser, p)
  await signIn(page, p)
  await page.goto('/account')
  await expect(page.locator('#sessions').getByText('current')).toBeVisible()
  await expect(page.locator('#sessions').getByRole('button', { name: 'Revoke' })).toHaveCount(2)

  await page.locator('#sessions').getByRole('button', { name: 'Revoke' }).first().click()
  await expect(page.getByText('Session revoked.')).toBeVisible()
  await expect(page.locator('#sessions').getByRole('button', { name: 'Revoke' })).toHaveCount(1)

  await page.getByRole('button', { name: 'Sign out all other sessions' }).click()
  await confirmDialog(page).click()
  await expect(page.getByText('Signed out all other sessions.')).toBeVisible()
  await expect(page.locator('#sessions').getByRole('button', { name: 'Revoke' })).toHaveCount(0)

  for (const other of [a, b]) {
    await other.goto('/account')
    await expect(other).toHaveURL(/\/login/)
    await other.context().close()
  }
  await page.reload()
  await expect(page).toHaveURL(/\/account$/)
})

test('API tokens: limited scopes, created behind the password, shown once, used, revoked', async ({ page }) => {
  const p = await newUser('employee', 'tokens')
  await signIn(page, p)
  await page.goto('/account')
  const panel = page.locator('#api-tokens')
  await expect(panel.getByText('No API tokens yet.')).toBeVisible()
  await panel.getByRole('button', { name: 'Create token' }).click()
  await panel.getByLabel('Token name').fill('e2e-script')
  await panel.getByRole('radio', { name: /Limited access/ }).check()
  const create = panel.getByRole('button', { name: 'Create', exact: true })
  await expect(create).toBeDisabled()
  await panel.getByRole('checkbox', { name: 'List & read shares' }).check()
  await create.click()

  await page.getByTestId('step-up-password').fill('not-my-password-1')
  await page.getByTestId('step-up-confirm').click()
  await expect(page.getByRole('dialog').getByRole('alert')).toHaveText('Password incorrect.')
  await page.getByTestId('step-up-password').fill(p.password)
  await page.getByTestId('step-up-confirm').click()

  const plaintext = (await panel.locator('pre.plaintext-token').innerText()).trim()
  expect(plaintext).toMatch(/^fh_[0-9a-f]{8}_/)
  const bearer = { authorization: `Bearer ${plaintext}`, 'content-type': 'application/json' }
  expect((await fetch(`${BASE}/api/shares?box=outbox`, { headers: bearer })).status).toBe(200)
  const outOfScope = await fetch(`${BASE}/api/shares`, {
    method: 'POST',
    headers: bearer,
    body: JSON.stringify({ kind: 'outbound', recipients: { user_ids: [], group_ids: [] }, expires_at: null }),
  })
  expect(outOfScope.status).toBe(403)
  await panel.getByRole('button', { name: "I've saved it" }).click()
  await expect(panel.locator('pre.plaintext-token')).toHaveCount(0)
  await expect(panel.getByText('e2e-script')).toBeVisible()

  await panel.getByRole('button', { name: 'Revoke' }).click()
  await confirmDialog(page).click()
  await expect(panel.getByText('No API tokens yet.')).toBeVisible()
  expect((await fetch(`${BASE}/api/shares?box=outbox`, { headers: bearer })).status).toBe(401)
})

test('notification preferences save per event', async ({ page }) => {
  const p = await newUser('client', 'prefs')
  await signIn(page, p)
  await page.goto('/account')
  const prefs = page.locator('#notifications')
  const select = prefs.getByRole('combobox').first()
  await expect(select).toBeVisible()
  const value = await select.inputValue()
  const next = value === 'in_app' ? 'email' : 'in_app'
  await select.selectOption(next)
  await expect(prefs.getByText('Saved.')).toBeVisible()
  await page.reload()
  await expect(page.locator('#notifications').getByRole('combobox').first()).toHaveValue(next)
})

test('passkeys: one is added behind the password and removed again', async ({ page }) => {
  const p = await newUser('client', 'passkey')
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('WebAuthn.enable')
  await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  })
  await signIn(page, p)
  await page.goto('/account')
  const panel = page.locator('#webauthn')
  await expect(panel.getByText('No passkeys registered yet.')).toBeVisible()
  await panel.getByRole('button', { name: 'Add a passkey' }).click()
  await panel.getByLabel('Label').fill('E2E key')
  await panel.getByTestId('webauthn-register').click()
  await page.getByTestId('step-up-password').fill(p.password)
  await page.getByTestId('step-up-confirm').click()
  await expect(page.getByText('Passkey added.')).toBeVisible()
  await expect(panel.getByText('E2E key')).toBeVisible()

  await panel.getByRole('button', { name: 'Remove' }).click()
  await confirmDialog(page).click()
  await expect(page.getByText('Passkey removed.')).toBeVisible()
  await expect(panel.getByText('No passkeys registered yet.')).toBeVisible()
})

test('an admin picks how the admin sidebar collapses', async ({ page }) => {
  const p = await newUser('admin', 'navmode')
  await signIn(page, p)
  await page.goto('/account')
  const section = page.locator('#admin-nav')
  await section.getByRole('button', { name: 'Always expanded' }).click()
  await expect(page.getByText('Admin sidebar preference saved.')).toBeVisible()
  await page.goto('/admin')
  const headers = page.locator('.nav-cat-header')
  await expect(headers).toHaveCount(6)
  for (const h of await headers.all()) await expect(h).toHaveAttribute('aria-expanded', 'true')

  await page.goto('/account')
  await section.getByRole('button', { name: 'Accordion' }).click()
  await expect(page.getByText('Admin sidebar preference saved.')).toBeVisible()
  // Accordion: only the open page's category.
  await page.goto('/admin/users')
  await expect(page.locator('.nav-cat-header[aria-expanded="true"]')).toHaveCount(1)
})
