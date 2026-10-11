import { expect, test, type Page } from '@playwright/test'

import { ADMIN, apiFetch, apiLogin, createUser, freshPassword } from '../helpers'

/* Passkey sign-in (v2.27.0), with Chromium's virtual authenticator standing in
 * for a laptop's built-in one: it keeps the passkey on the "device" (a
 * discoverable credential) and verifies the user (PIN / biometric), the two
 * things signing in with a passkey alone rests on.
 *
 * The admin switch is turned back OFF at the end whatever happens: it is off by
 * default, and the journeys and the docs tour after this one sign in with
 * passwords alone. */

async function addVirtualAuthenticator(page: Page): Promise<void> {
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
}

async function setPasskeySignin(adminToken: string, on: boolean): Promise<void> {
  const current = await apiFetch(adminToken, '/api/admin/settings/twofa')
  if (!current.ok) throw new Error(`[e2e] read 2FA policy failed: ${current.status}`)
  const policy = await current.json()
  const r = await apiFetch(adminToken, '/api/admin/settings/twofa', {
    method: 'PUT',
    body: JSON.stringify({
      required_roles: policy.required_roles,
      required_group_ids: policy.required_group_ids,
      passkey_signin: on,
    }),
  })
  if (!r.ok) throw new Error(`[e2e] set passkey sign-in ${on} failed: ${r.status} ${await r.text()}`)
}

/** Chromium's virtual authenticator picks the passkey for an autofill
 *  (`mediation: 'conditional'`) request by itself, which a person does from the
 *  email field's suggestions. Pages loaded after this see no autofill support,
 *  so the button and the password form can be driven. */
async function withoutAutofill(page: Page): Promise<void> {
  await page.addInitScript(() => {
    Object.defineProperty(window.PublicKeyCredential, 'isConditionalMediationAvailable', {
      value: async () => false,
    })
  })
}

/** How many times `email` has signed in with a passkey alone (the audit trail's
 *  `via`), so a step that ends signed in proves which way it got there. */
async function passkeySignIns(adminToken: string, email: string): Promise<number> {
  const r = await apiFetch(adminToken, '/api/admin/audit-log?event_type=login_success&page_size=200')
  if (!r.ok) throw new Error(`[e2e] audit log failed: ${r.status}`)
  const rows: { actor_email: string | null; extra: { via?: string } | null }[] = (await r.json())
    .items
  return rows.filter((row) => row.actor_email === email && row.extra?.via === 'passkey').length
}

async function signOut(page: Page): Promise<void> {
  // The access token lives only in memory and the refresh token in a cookie:
  // dropping the cookie and reloading leaves nothing signed in.
  await page.context().clearCookies()
  await page.goto('/login')
}

test('a passkey signs in on its own, and finishes a password sign-in', async ({ page }) => {
  const admin = await apiLogin(ADMIN.email, ADMIN.password)
  const email = `passkey+${Date.now()}@e2e.local`
  const password = freshPassword()
  await createUser(admin, { email, password, role: 'client', displayName: 'Passkey User' })
  await addVirtualAuthenticator(page)

  try {
    // 1. Sign in with the password and add a passkey (confirmed with the
    //    password in the step-up dialog).
    await page.goto('/login')
    await page.fill('#login-email', email)
    await page.fill('#login-password', password)
    await page.click('button[type=submit]')
    await expect(page).not.toHaveURL(/\/login(\?|$)/)

    await page.goto('/account')
    await page.getByRole('button', { name: 'Add a passkey' }).click()
    await page.locator('.add-form input').fill('Virtual laptop')
    await page.getByTestId('webauthn-register').click()
    await page.getByTestId('step-up-password').fill(password)
    await page.getByTestId('step-up-confirm').click()
    await expect(page.locator('.cred-name', { hasText: 'Virtual laptop' })).toBeVisible()

    // 2. The admin switches passkey sign-in on.
    await setPasskeySignin(admin, true)

    // 3. A passkey alone: no email, no password - first from the email
    //    field's autofill, then from the button.
    expect(await passkeySignIns(admin, email)).toBe(0)
    await signOut(page)
    await expect(page).not.toHaveURL(/\/login(\?|$)/)
    expect(await passkeySignIns(admin, email)).toBe(1)

    await withoutAutofill(page)
    await signOut(page)
    await page.getByTestId('passkey-signin').click()
    await expect(page).not.toHaveURL(/\/login(\?|$)/)
    expect(await passkeySignIns(admin, email)).toBe(2)

    // 4. The passkey now counts as this account's second factor, so the
    //    password alone does not finish - the passkey step does.
    await signOut(page)
    await page.fill('#login-email', email)
    await page.fill('#login-password', password)
    await page.click('button[type=submit]')
    await expect(page.getByTestId('passkey-step')).toBeVisible()
    await expect(page).toHaveURL(/\/login(\?|$)/)
    await page.click('button[type=submit]')
    await expect(page).not.toHaveURL(/\/login(\?|$)/)
  } finally {
    await setPasskeySignin(admin, false)
  }
})
