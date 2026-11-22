import type { Page } from '@playwright/test'

import {
  THE_ADMIN,
  browserOf,
  confirm,
  expect,
  given,
  mailed,
  mailedToken,
  openUser,
  pathOf,
  pickRecipient,
  signIn,
  signOut,
  test,
  toast,
  tryToSignIn,
  uid,
} from './lib'

/* Accounts, from the admin's hand to the person's (COVERAGE.c.md, U1-U10).
 * Each test is one hand-over followed to the end: the admin acts on a page,
 * and the person the account belongs to then does what follows from it - in
 * their own browser, through the sign-in form, with the very link or password
 * they were given. */

const WRONG = "That email and password don't match."
const fresh = (word: string) => `E2e!${word}${uid()}Qz`

async function inviteForm(adminPage: Page): Promise<void> {
  await adminPage.goto('/admin/users')
  await adminPage.getByRole('button', { name: /invite user/i }).click()
  await expect(adminPage.getByRole('heading', { name: 'Invite a new user' })).toBeVisible()
}

const inviteRow = (adminPage: Page, email: string) =>
  adminPage.locator('.invites-table tr', { hasText: email })

/** The admin invites `email` by mail; the invite is listed as pending. */
async function invite(adminPage: Page, email: string, role: string, groupName?: string) {
  await inviteForm(adminPage)
  await adminPage.getByRole('textbox', { name: /^Email/ }).fill(email)
  await adminPage.getByRole('textbox', { name: /^Display name/ }).fill('Ina Invited')
  await adminPage.getByRole('combobox', { name: /^Role/ }).selectOption(role)
  if (groupName) await adminPage.getByRole('checkbox', { name: groupName }).check()
  await adminPage.getByRole('button', { name: 'Send invite' }).click()
  await expect(inviteRow(adminPage, email)).toContainText('pending')
}

/** The invitee fills in the registration page a link opened. */
async function register(page: Page, name: string, password: string): Promise<void> {
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your account' })).toBeVisible()
  const form = page.locator('form')
  await form.getByLabel('Display name').fill(name)
  await form.getByLabel('New password').fill(password)
  await form.getByRole('button', { name: 'Create account' }).click()
}

test.describe('an invitation', () => {
  test('U1 the invitee registers from their own mail and lands in the app their role gets', async ({
    page,
    browser,
  }) => {
    const s = uid()
    const email = `invitee-${s}@e2e.example`
    const groupName = `Welcome ${s}`
    const colleague = await given.user('client', 'welcomed')
    await given.group(groupName, [colleague.id])

    const adminPage = await browserOf(browser, THE_ADMIN)
    await invite(adminPage, email, 'employee', groupName)

    const password = fresh('invited')
    await page.goto(`/register/${await mailedToken('register', email)}`)
    await register(page, 'Ina Invited', password)
    await expect(page).not.toHaveURL(/\/register\//)

    // An employee's app: they can compose, and the group they were invited
    // into is theirs to address - with the client in it.
    await page.getByRole('navigation', { name: 'Menu' }).getByRole('link', { name: 'New share' }).click()
    await pickRecipient(page, groupName)
    await pickRecipient(page, colleague.name)
    await expect(page.locator('.recipient-picker .chip')).toHaveCount(2)

    // The admin sees an account, no longer an open invitation.
    await adminPage.reload()
    await expect(inviteRow(adminPage, email)).toHaveCount(0)
    await openUser(adminPage, email)
    await expect(adminPage.getByLabel('Role')).toHaveValue('employee')

    // And the password they chose is the one that signs them in.
    await signOut(page)
    await signIn(page, { email, password })
    await adminPage.context().close()
  })

  test('U1 a new link replaces the old one, and a resent mail replaces that', async ({
    page,
    browser,
  }) => {
    const email = `relinked-${uid()}@e2e.example`
    const adminPage = await browserOf(browser, THE_ADMIN)
    await invite(adminPage, email, 'client')
    const first = await mailedToken('register', email)

    await inviteRow(adminPage, email).getByRole('button', { name: 'New link' }).click()
    await confirm(adminPage)
    const second = pathOf(await adminPage.getByLabel('New invite link').inputValue())
    expect(second).toMatch(/^\/register\/[A-Za-z0-9._~-]+$/)
    expect(second).not.toBe(`/register/${first}`)

    // The link in the first mail is dead.
    await page.goto(`/register/${first}`)
    await register(page, 'Too Late', fresh('late'))
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page).toHaveURL(/\/register\//)

    // "Resend by email" mails a third one, and the admin's copied link dies too.
    await inviteRow(adminPage, email).getByRole('button', { name: 'Resend by email' }).click()
    await expect(toast(adminPage, 'Invite resent')).toBeVisible()
    await expect
      .poll(async () => `/register/${await mailedToken('register', email)}`)
      .not.toBe(`/register/${first}`)
    const third = await mailedToken('register', email)
    expect(`/register/${third}`).not.toBe(second)

    await page.goto(second)
    await register(page, 'Still Late', fresh('late'))
    await expect(page.getByRole('alert')).toBeVisible()

    const password = fresh('resent')
    await page.goto(`/register/${third}`)
    await register(page, 'Rita Resent', password)
    await expect(page).not.toHaveURL(/\/register\//)
    await signOut(page)
    await signIn(page, { email, password })
    await adminPage.context().close()
  })
})

test('U2 an account the admin creates with a password signs in with it, and changes it', async ({
  page,
  browser,
}) => {
  const email = `direct-${uid()}@e2e.example`
  const given_password = fresh('given')
  const adminPage = await browserOf(browser, THE_ADMIN)
  await inviteForm(adminPage)
  await adminPage.getByRole('textbox', { name: /^Email/ }).fill(email)
  await adminPage.getByRole('textbox', { name: /^Display name/ }).fill('Dora Direct')
  await adminPage.getByRole('checkbox', { name: /^Create the account now/ }).check()
  await adminPage.getByLabel(/^Password/).fill(given_password)
  await adminPage.getByRole('button', { name: 'Create user' }).click()
  await expect(toast(adminPage, `${email} created`)).toBeVisible()

  // Through the sign-in form, with the password the admin typed.
  await signIn(page, { email, password: given_password })
  await page.goto('/account')
  await expect(page.getByRole('heading', { level: 1, name: 'Dora Direct' })).toBeVisible()

  const own = fresh('own')
  await page.locator('#acc-cur').fill(given_password)
  await page.locator('#acc-new').fill(own)
  await page.getByRole('button', { name: 'Change password' }).click()
  await expect(page.getByText('Password changed. Other sessions revoked.')).toBeVisible()

  await signOut(page)
  await tryToSignIn(page, email, given_password)
  await expect(page.getByRole('alert')).toHaveText(WRONG)
  await signIn(page, { email, password: own })
  await adminPage.context().close()
})

test('U3 an invitation the admin activates: the admin is handed the link with which the person gets in', async ({
  page,
  browser,
}) => {
  const email = `activated-${uid()}@e2e.example`
  const adminPage = await browserOf(browser, THE_ADMIN)
  await invite(adminPage, email, 'employee')
  const invitation = await mailedToken('register', email)

  await inviteRow(adminPage, email).getByRole('button', { name: 'Activate' }).click()
  const dialog = adminPage.getByRole('dialog', { name: 'Activate account directly' })
  // The account gets no usable password. The admin is told so - not that the
  // person "can sign in immediately" - and is handed the link that sets one.
  await expect(dialog).toContainText('no password yet')
  await dialog.getByLabel('Display name').fill('Ada Activated')
  await dialog.getByRole('button', { name: 'Activate now' }).click()
  await expect(toast(adminPage, 'Account activated')).toBeVisible()
  await expect(toast(adminPage, 'Account activated')).not.toContainText('sign in immediately')
  await expect(inviteRow(adminPage, email)).toHaveCount(0)
  const handedOver = adminPage.getByTestId('activated-link')
  await expect(handedOver).toContainText(email)
  const link = pathOf(await handedOver.getByLabel('Set-password link').inputValue())
  expect(link).toMatch(/^\/reset-password\/[A-Za-z0-9._~-]+$/)

  // The invitation is spent.
  await page.goto(`/register/${invitation}`)
  await register(page, 'Too Late', fresh('late'))
  await expect(page.getByRole('alert')).toBeVisible()

  // The person opens what the admin was handed, and is in: as an employee.
  const password = fresh('first')
  await page.goto(link)
  await page.getByLabel('New password', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Reset password' }).click()
  await expect(page.getByRole('heading', { name: 'Password reset.' })).toBeVisible()
  await signIn(page, { email, password })
  await expect(page.getByRole('navigation', { name: 'Menu' }).getByRole('link', { name: 'Sent' })).toBeVisible()
  await page.goto('/account')
  await expect(page.getByRole('heading', { level: 1, name: 'Ada Activated' })).toBeVisible()
  // The link worked once.
  const again = await browserOf(browser)
  await again.goto(link)
  await again.getByLabel('New password', { exact: true }).fill(fresh('second'))
  await again.getByRole('button', { name: 'Reset password' }).click()
  await expect(again.getByRole('alert')).toBeVisible()
  await again.context().close()
  await adminPage.context().close()
})

test('U4 a reset the admin forces: the person opens the link the admin was given', async ({
  page,
  browser,
}) => {
  const person = await given.user('client', 'forced')
  const elsewhere = await browserOf(browser, person) // their other device
  const adminPage = await browserOf(browser, THE_ADMIN)
  await openUser(adminPage, person.email)
  await adminPage.getByRole('button', { name: 'Force password reset' }).click()
  await confirm(adminPage)
  await expect(adminPage.getByText('Password reset link')).toBeVisible()
  // A LINK, as the heading says - something the admin can pass on.
  const link = pathOf(await adminPage.locator('.reset-box .token').first().innerText())
  expect(link).toMatch(/^\/reset-password\/[A-Za-z0-9._~-]+$/)

  // Until it is used, the old password still works (the page says so).
  await signIn(page, person)
  await signOut(page)

  const password = fresh('forced')
  await page.goto(link)
  await page.getByLabel('New password', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Reset password' }).click()
  await expect(page.getByRole('heading', { name: 'Password reset.' })).toBeVisible()

  await tryToSignIn(page, person.email, person.password)
  await expect(page.getByRole('alert')).toHaveText(WRONG)
  await signIn(page, { email: person.email, password })
  // The reset signed their other device out.
  await elsewhere.goto('/account')
  await expect(elsewhere).toHaveURL(/\/login/)
  await elsewhere.context().close()
  await adminPage.context().close()
})

test('U5 a person locks themselves out, is warned by mail, and signs in once the admin unlocks them', async ({
  page,
  browser,
}) => {
  test.setTimeout(120_000)
  const person = await given.user('client', 'lockedout')
  for (let i = 0; i < 5; i++) {
    await tryToSignIn(page, person.email, `wrong-password-${i}-xx`)
    await expect(page.getByRole('alert')).toBeVisible()
  }
  await tryToSignIn(page, person.email, person.password)
  await expect(page.getByRole('alert')).toHaveText(/temporarily locked/)
  await mailed(person.email, /^Subject:.*(locked|sign-in attempts)/im)

  const adminPage = await browserOf(browser, THE_ADMIN)
  await openUser(adminPage, person.email)
  await expect(adminPage.getByTestId('locked-pill')).toBeVisible()
  await adminPage.getByTestId('unlock-btn').click()
  await confirm(adminPage)
  await expect(toast(adminPage, 'Account unlocked.')).toBeVisible()
  await expect(adminPage.getByTestId('locked-pill')).toHaveCount(0)

  await signIn(page, person)
  await adminPage.context().close()
})

test('U6 a disabled account is signed out and refused, and signs in again once re-enabled', async ({
  page,
  browser,
}) => {
  const person = await given.user('employee', 'paused')
  await signIn(page, person)
  await page.goto('/account')

  const adminPage = await browserOf(browser, THE_ADMIN)
  await openUser(adminPage, person.email)
  await adminPage.getByLabel('Disabled (revoke all sessions)').check()
  await adminPage.getByRole('button', { name: 'Save' }).click()
  await expect(adminPage.locator('.meta-line')).toContainText('disabled')

  // Their open tab is out at its next load, and the form refuses them.
  await page.goto('/inbox')
  await expect(page).toHaveURL(/\/login/)
  await tryToSignIn(page, person.email, person.password)
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page).toHaveURL(/\/login/)

  await adminPage.getByLabel('Disabled (revoke all sessions)').uncheck()
  await adminPage.getByRole('button', { name: 'Save' }).click()
  await expect(adminPage.locator('.meta-line')).not.toContainText('disabled')
  await signIn(page, person)
  await adminPage.context().close()
})

test('U7 a role the admin changes is the app the person gets', async ({ page, browser }) => {
  const person = await given.user('client', 'promoted')
  await signIn(page, person)
  // A client submits to the company: no recipients to pick.
  await page.goto('/share/new')
  await expect(page.getByRole('heading', { level: 1, name: 'New share' })).toBeVisible()
  await expect(page.locator('.recipient-picker')).toHaveCount(0)

  const adminPage = await browserOf(browser, THE_ADMIN)
  await openUser(adminPage, person.email)
  await adminPage.getByLabel('Role').selectOption('employee')
  await adminPage.getByRole('button', { name: 'Save' }).click()
  // Wait for what the admin sees before leaving the page: a reload in the same
  // instant cancels the save on its way out (it did, on a slower machine).
  await expect(toast(adminPage, 'Saved.')).toBeVisible()
  await adminPage.reload()
  await expect(adminPage.getByLabel('Role')).toHaveValue('employee')

  // An employee addresses people.
  await page.reload()
  await expect(page.locator('.recipient-picker')).toBeVisible()

  await adminPage.getByLabel('Role').selectOption('client')
  await adminPage.getByRole('button', { name: 'Save' }).click()
  await expect(toast(adminPage, 'Saved.')).toBeVisible()
  await adminPage.reload()
  await expect(adminPage.getByLabel('Role')).toHaveValue('client')
  await page.reload()
  await expect(page.getByRole('heading', { level: 1, name: 'New share' })).toBeVisible()
  await expect(page.locator('.recipient-picker')).toHaveCount(0)
  await adminPage.context().close()
})

test('U8 an address the admin changes is the one that signs in: at once, and by the confirmation link', async ({
  page,
  browser,
}) => {
  const person = await given.user('client', 'moved')
  const adminPage = await browserOf(browser, THE_ADMIN)
  await openUser(adminPage, person.email)

  const now = `now-${uid()}@e2e.example`
  await adminPage.getByLabel('Change email').fill(now)
  await adminPage.getByLabel('Apply immediately (skip verification)').check()
  await adminPage.getByRole('button', { name: 'Change email' }).click()
  await confirm(adminPage)
  await expect(toast(adminPage, 'Email changed.')).toBeVisible()

  await tryToSignIn(page, person.email, person.password)
  await expect(page.getByRole('alert')).toHaveText(WRONG)
  await signIn(page, { email: now, password: person.password })
  await signOut(page)

  // With verification: nothing changes until the link is opened.
  const later = `later-${uid()}@e2e.example`
  await adminPage.getByLabel('Change email').fill(later)
  await adminPage.getByLabel('Apply immediately (skip verification)').uncheck()
  await adminPage.getByRole('button', { name: 'Change email' }).click()
  await confirm(adminPage)
  await expect(adminPage.getByText('Confirmation link', { exact: true })).toBeVisible()
  const link = pathOf(await adminPage.locator('.reset-box .token').first().innerText())
  expect(link).toMatch(/^\/confirm-email-change\//)

  await tryToSignIn(page, later, person.password)
  await expect(page.getByRole('alert')).toHaveText(WRONG)
  await page.goto(link)
  await expect(page.getByRole('heading', { name: 'Email changed.' })).toBeVisible()
  await tryToSignIn(page, now, person.password)
  await expect(page.getByRole('alert')).toHaveText(WRONG)
  await signIn(page, { email: later, password: person.password })
  await adminPage.context().close()
})

test('U9 a session the admin revokes signs that device out; "Revoke all" signs out the rest', async ({
  page,
  browser,
}) => {
  const person = await given.user('employee', 'revoked')
  await signIn(page, person)
  const second = await browserOf(browser, person)
  // Loaded afresh, a device either shows the account page or lands on sign-in;
  // which one is known once that page's heading is up.
  const signedOut = async (p: Page) => {
    await p.goto('/account')
    await p.getByRole('heading', { level: 1 }).first().waitFor()
    return /\/login/.test(p.url())
  }

  const adminPage = await browserOf(browser, THE_ADMIN)
  await openUser(adminPage, person.email)
  const sessions = adminPage.locator('.session-item')
  await expect(sessions).toHaveCount(2)
  await sessions.first().getByRole('button', { name: 'Revoke' }).click()
  await expect(sessions).toHaveCount(1)
  // Exactly one of the two devices is out.
  expect([await signedOut(page), await signedOut(second)].filter(Boolean)).toHaveLength(1)

  await adminPage.getByRole('button', { name: 'Revoke all' }).click()
  await confirm(adminPage)
  await expect(adminPage.getByText('No active sessions.')).toBeVisible()
  expect(await signedOut(page)).toBe(true)
  expect(await signedOut(second)).toBe(true)

  await signIn(page, person)
  await second.context().close()
  await adminPage.context().close()
})

test('U10 an erased person can no longer sign in, and what they sent is gone for its recipient', async ({
  page,
  browser,
}) => {
  const sender = await given.user('employee', 'erased')
  const recipient = await given.user('client', 'kept')
  await given.connected(sender, recipient)
  const subject = `From the erased ${uid()}`
  await given.share(sender, {
    users: [recipient.id],
    subject,
    files: { 'memo.txt': 'memo' },
  })
  const theirs = await browserOf(browser, recipient)
  await theirs.goto('/inbox')
  await expect(theirs.getByText(subject)).toBeVisible()

  const adminPage = await browserOf(browser, THE_ADMIN)
  await openUser(adminPage, sender.email)
  const erase = adminPage.getByTestId('erase-user')
  await erase.click()
  await expect(adminPage.locator('.erase-preflight')).toBeVisible()
  await erase.click()
  const receipt = adminPage.waitForEvent('download')
  await adminPage.getByTestId('step-up-password').fill(THE_ADMIN.password)
  await adminPage.getByTestId('step-up-confirm').click()
  await receipt
  await expect(toast(adminPage, 'User erased')).toBeVisible()

  await tryToSignIn(page, sender.email, sender.password)
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page).toHaveURL(/\/login/)

  // The recipient's copy: off the list of what they can still use; under
  // "Any state" it is there as revoked, from nobody, with no file left in it.
  await theirs.goto('/inbox')
  await expect(theirs.getByRole('heading', { level: 1, name: 'Received shares' })).toBeVisible()
  const row = theirs.locator('tbody tr', { hasText: subject })
  await expect(theirs.getByText('Loading…')).toHaveCount(0)
  await expect(row).toHaveCount(0)
  await theirs.getByRole('combobox', { name: 'Any state' }).selectOption('')
  await expect(row).toContainText('[erased]')
  await expect(row).toContainText(/revoked/i)
  await expect(row).not.toContainText(sender.name)
  await theirs.getByText(subject).click()
  await expect(theirs.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  await expect(theirs.getByText('No files in this share.')).toBeVisible()
  await expect(theirs.getByRole('button', { name: 'Download', exact: true })).toHaveCount(0)
  await theirs.context().close()
  await adminPage.context().close()
})
