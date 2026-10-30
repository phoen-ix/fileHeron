import type { Page } from '@playwright/test'
import { generateSync } from 'otplib'

import { apiFetch, enroll2FA } from '../../helpers'
import {
  admin,
  expect,
  mailedToken,
  newUser,
  ok,
  restoreSettings,
  setSettings,
  signIn,
  test,
  uid,
} from './lib'

/* Signing in and out, the password-reset and invite links, the account lock,
 * the sign-in language switch, and two-factor authentication end to end:
 * enrolment, sign-in with a code and with a recovery code, regenerating the
 * codes and turning it off - all through the UI. */

async function signOut(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Menu' }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(page).toHaveURL(/\/login(\?|$)/)
}

async function fillCreds(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Continue' }).click()
}

test('sign-out from the user menu ends the session', async ({ page }) => {
  const p = await newUser('client', 'signout')
  await signIn(page, p)
  await signOut(page)
  await page.goto('/account')
  await expect(page).toHaveURL(/\/login\?redirect=(%2F|\/)account$/)
})

test('the header and, on a phone, the user menu reach every user page', async ({ page }) => {
  const p = await newUser('employee', 'nav')
  await signIn(page, p)
  const nav = page.getByRole('navigation', { name: 'Menu' })
  for (const [link, heading] of [
    ['Sent', 'Sent'],
    ['Received', 'Received'],
    ['New share', 'New share'],
  ]) {
    await nav.getByRole('link', { name: link }).click()
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
  }
  await page.getByRole('button', { name: 'Menu' }).click()
  await page.getByRole('menuitem', { name: 'Account' }).click()
  await expect(page.getByRole('heading', { level: 1, name: p.name })).toBeVisible()

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(nav).toBeHidden()
  await page.getByRole('button', { name: 'Menu' }).click()
  await page.getByRole('menuitem', { name: 'Received' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Received' })).toBeVisible()
})

test('the sign-in page switches to German and back', async ({ page }) => {
  await page.goto('/login')
  await page.getByRole('button', { name: 'Deutsch' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Willkommen zurück.' })).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await expect(page.getByLabel('E-Mail', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'English' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Welcome back.' })).toBeVisible()
})

test('a wrong password is refused with a message', async ({ page }) => {
  const p = await newUser('client', 'wrongpw')
  await fillCreds(page, p.email, 'not-the-password-123')
  await expect(page.getByRole('alert')).toHaveText("That email and password don't match.")
  await expect(page).toHaveURL(/\/login$/)
})

test('five wrong passwords lock the account, even against the right one', async ({ page }) => {
  // The locking attempt took ~50 s on MariaDB until part b's fix(backend) "a sign-in that
  // locks an account no longer hangs for 50 s"; the generous wait covers a stack without it.
  test.setTimeout(120_000)
  const p = await newUser('client', 'lock')
  for (let i = 0; i < 5; i++) {
    await fillCreds(page, p.email, `wrong-password-${i}-xx`)
    await expect(page.getByRole('alert')).toBeVisible({ timeout: i === 4 ? 70_000 : 10_000 })
  }
  await fillCreds(page, p.email, p.password)
  await expect(page.getByRole('alert')).toHaveText(/temporarily locked/)
  await expect(page).toHaveURL(/\/login$/)
})

test('forgot password: the mailed link resets it once, and only the new one signs in', async ({
  page,
}) => {
  const p = await newUser('client', 'forgot')
  await page.goto('/login')
  await page.getByRole('link', { name: 'Forgot password' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Forgot password' })).toBeVisible()
  await page.getByLabel('Email', { exact: true }).fill(p.email)
  await page.getByRole('button', { name: 'Send reset link' }).click()
  await expect(page.getByRole('heading', { name: 'Check your inbox.' })).toBeVisible()

  const token = await mailedToken('reset-password', p.email)
  const fresh = `E2e!reset${uid()}Qz`
  await page.goto(`/reset-password/${token}`)
  await page.getByLabel('New password', { exact: true }).fill(fresh)
  await page.getByRole('button', { name: 'Reset password' }).click()
  await expect(page.getByRole('heading', { name: 'Password reset.' })).toBeVisible()

  // The link is spent.
  await page.goto(`/reset-password/${token}`)
  await page.getByLabel('New password', { exact: true }).fill(`E2e!again${uid()}Qz`)
  await page.getByRole('button', { name: 'Reset password' }).click()
  await expect(page.getByRole('alert')).toBeVisible()

  await fillCreds(page, p.email, p.password)
  await expect(page.getByRole('alert')).toHaveText("That email and password don't match.")
  await signIn(page, { email: p.email, password: fresh })
})

test('forgot password answers an unknown address exactly like a known one', async ({ page }) => {
  await page.goto('/forgot-password')
  await page.getByLabel('Email', { exact: true }).fill(`nobody-${uid()}@e2e.example`)
  await page.getByRole('button', { name: 'Send reset link' }).click()
  await expect(page.getByRole('heading', { name: 'Check your inbox.' })).toBeVisible()
})

test('an invite registers in German and signs the new account in', async ({ page }) => {
  const email = `invitee-${uid()}@e2e.example`
  await ok(
    apiFetch(await admin(), '/api/account/invite', {
      method: 'POST',
      body: JSON.stringify({ email, target_role: 'client', display_name_hint: 'Invitee' }),
    }),
    'invite',
  )
  const token = await mailedToken('register', email)
  await page.goto(`/register/${token}`)
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your account' })).toBeVisible()
  const form = page.locator('form')
  await form.getByLabel('Display name').fill('Ilse Invitee')
  await form.getByLabel('New password').fill(`E2e!invite${uid()}Qz`)
  await form.getByRole('button', { name: 'Deutsch' }).click()
  await form.getByRole('button', { name: 'Create account' }).click()
  await expect(page).not.toHaveURL(/\/register\//)
  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
  await expect(page.getByRole('navigation', { name: 'Menü' })).toBeVisible()

  // The link is single-use.
  await page.context().clearCookies()
  await page.goto(`/register/${token}`)
  await page.locator('form').getByLabel(/Anzeigename|Display name/).fill('Again')
  await page.locator('form').getByLabel(/Neues Passwort|New password/).fill(`E2e!twice${uid()}Qz`)
  await page.locator('form button[type=submit]').click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page).toHaveURL(/\/register\//)
})

test('an invalid invite link is refused on submit', async ({ page }) => {
  await page.goto('/register/not-a-real-invite')
  const form = page.locator('form')
  await form.getByLabel('Display name').fill('Nobody')
  await form.getByLabel('New password').fill(`E2e!invalid${uid()}Qz`)
  await form.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('alert')).toHaveText(/invite link is not valid/)
})

/** The TOTP code `steps` 30-second steps from now. The server accepts ±1 step
 * and refuses a step it has already seen, so a second action inside the same
 * step uses the next one. */
const code = (secret: string, steps = 0): string =>
  generateSync({ secret, epoch: Math.floor(Date.now() / 1000) + steps * 30 })

test('two-factor: enrol, sign in with a code and a recovery code, regenerate, turn off', async ({
  page,
}) => {
  test.setTimeout(180_000)
  const p = await newUser('client', 'totp')
  await signIn(page, p)

  // Enrol from the account page.
  await page.goto('/account')
  await expect(page.getByText('Two-factor auth is off.')).toBeVisible()
  await page.getByRole('link', { name: 'Set up two-factor auth' }).click()
  await expect(page).toHaveURL(/\/account\/2fa$/)
  await page.getByRole('button', { name: 'Set up two-factor auth' }).click()
  await expect(page.locator('.qr-svg svg')).toBeVisible()
  const secret = (await page.locator('.secret-mono').innerText()).replace(/\s+/g, '')
  await page.getByLabel('Verify').fill('000000')
  await page.getByRole('button', { name: 'Enable two-factor' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await page.getByLabel('Verify').fill(code(secret))
  await page.getByRole('button', { name: 'Enable two-factor' }).click()
  await expect(page.getByRole('heading', { name: 'Save these recovery codes.' })).toBeVisible()
  const codes = await page.locator('.code-cell').allInnerTexts()
  expect(codes).toHaveLength(10)
  const finish = page.getByRole('button', { name: 'Continue' })
  await expect(finish).toBeDisabled()
  await page.getByLabel("I've saved them").check()
  await finish.click()
  await expect(page.getByText('Two-factor is active.')).toBeVisible()
  await expect(page.getByText('10 recovery codes left')).toBeVisible()

  // A password alone no longer signs in; a wrong code is refused, the right one works.
  await signOut(page)
  await fillCreds(page, p.email, p.password)
  await expect(page.getByLabel('Authentication code')).toBeVisible()
  await page.getByLabel('Authentication code').fill('000000')
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByRole('alert')).toHaveText('That code is incorrect or expired.')
  await page.getByLabel('Authentication code').fill(code(secret))
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page).not.toHaveURL(/\/login(\?|$)/)

  // A recovery code works once. Checking one runs Argon2 against every unused
  // code (up to ten), which takes seconds on a loaded host.
  const recoveryCheck = { timeout: 30_000 }
  for (const expectIn of [true, false]) {
    await signOut(page)
    await fillCreds(page, p.email, p.password)
    await page.getByLabel('Authentication code').fill(codes[0])
    await page.getByRole('button', { name: 'Continue' }).click()
    if (expectIn) await expect(page).not.toHaveURL(/\/login(\?|$)/, recoveryCheck)
    else await expect(page.getByRole('alert')).toBeVisible(recoveryCheck)
  }
  await page.getByLabel('Authentication code').fill(codes[1])
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page).not.toHaveURL(/\/login(\?|$)/, recoveryCheck)

  // Regenerate: the old codes stop working, the new ones are shown once.
  await page.goto('/account/2fa')
  await expect(page.getByText('8 recovery codes left')).toBeVisible()
  await page.getByLabel('Current password').fill(p.password)
  await page.getByLabel('Six-digit code or recovery code').fill(code(secret, 1))
  await page.getByRole('button', { name: 'Regenerate recovery codes' }).click()
  await expect(page.getByRole('heading', { name: 'Save these recovery codes.' })).toBeVisible()
  const fresh = await page.locator('.code-cell').allInnerTexts()
  expect(fresh).toHaveLength(10)
  expect(fresh).not.toContain(codes[2])
  await page.getByLabel("I've saved them").check()
  await page.getByRole('button', { name: 'Continue' }).click()

  // Turn it off with a new recovery code; the password alone signs in again.
  await page.getByRole('button', { name: 'Turn off two-factor' }).click()
  await page.getByLabel('Current password').fill(p.password)
  await page.getByLabel('Six-digit code or recovery code').fill(fresh[0])
  await page.getByRole('button', { name: 'Turn off two-factor' }).click()
  await expect(page.getByText('Two-factor disabled.')).toBeVisible(recoveryCheck)
  await signOut(page)
  await signIn(page, p)
  await page.goto('/account')
  await expect(page.getByText('Two-factor auth is off.')).toBeVisible()
})

test('the announcement the admin sets shows on the sign-in page', async ({ page }) => {
  const text = `Maintenance tonight ${uid()}`
  const before = await setSettings('/api/admin/settings/motd', { enabled: true, text })
  try {
    await page.goto('/login')
    await expect(page.getByRole('note', { name: 'Site announcement' })).toHaveText(text)
  } finally {
    await restoreSettings('/api/admin/settings/motd', before)
  }
  await page.reload()
  await expect(page.getByRole('note', { name: 'Site announcement' })).toHaveCount(0)
})

test('a disabled account cannot sign in', async ({ page }) => {
  const p = await newUser('client', 'disabled')
  await ok(
    apiFetch(await admin(), `/api/admin/users/${p.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ is_disabled: true }),
    }),
    'disable',
  )
  await fillCreds(page, p.email, p.password)
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page).toHaveURL(/\/login$/)
})

test('a registered passkey stands in for the authenticator code', async ({ page }) => {
  const p = await newUser('employee', 'passkey2fa')
  const secret = await enroll2FA(p.email, p.password)
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
  // Register it through the account page (needs a signed-in session: the code).
  await fillCreds(page, p.email, p.password)
  await page.getByLabel('Authentication code').fill(code(secret))
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
  await page.goto('/account')
  await page.getByRole('button', { name: 'Add a passkey' }).click()
  await page.locator('#webauthn').getByLabel('Label').fill('Laptop')
  await page.getByTestId('webauthn-register').click()
  await page.getByTestId('step-up-password').fill(p.password)
  await page.getByTestId('step-up-confirm').click()
  await expect(page.getByText('Passkey added.')).toBeVisible()

  await signOut(page)
  await fillCreds(page, p.email, p.password)
  await expect(page.getByLabel('Authentication code')).toBeVisible()
  await page.getByRole('button', { name: 'Use a passkey instead' }).click()
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
})
