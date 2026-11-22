import type { Page } from '@playwright/test'

import {
  THE_ADMIN,
  authenticatorCode,
  browserOf,
  download,
  expect,
  expectedRefusal,
  file,
  given,
  openUser,
  pickRecipient,
  receivedUnderAnyState,
  signIn,
  signOut,
  test,
  toast,
  tryToSignIn,
  uid,
} from './lib'

/* Policies, from the admin's settings page to someone else's screen
 * (COVERAGE.c.md, U17-U22). The admin sets each one in the UI; the effect is
 * read in the browser of the person it applies to. The other specs set these
 * through the API, or check the effect through it. Each test puts the setting
 * back (through the API - cleaning up is not the use case). */

/** Change a setting for the length of `use`, then put back what was there. */
async function during(path: string, use: () => Promise<void>): Promise<void> {
  const before = await given.settings(path, {})
  try {
    await use()
  } finally {
    await given.settingsBack(path, before)
  }
}

async function send(page: Page, subject: string, name = 'note.txt', text = 'a note'): Promise<void> {
  await page.locator('input.dropzone-input').setInputFiles(file(name, text))
  await page.getByLabel('Subject', { exact: true }).fill(subject)
  await page.getByRole('button', { name: 'Create + send' }).click()
}

test('U17 two-factor required for a role: the person is sent to enrol, finishes, and signs in with a code', async ({
  page,
  browser,
}) => {
  test.setTimeout(180_000)
  await during('/api/admin/settings/twofa', async () => {
    const person = await given.user('client', 'mustenrol')
    const adminPage = await browserOf(browser, THE_ADMIN)
    await adminPage.goto('/admin/settings/twofa')
    await adminPage.getByRole('checkbox', { name: 'Clients', exact: true }).check()
    await adminPage.getByRole('button', { name: 'Save' }).click()
    await expect(toast(adminPage, '2FA enforcement policy saved.')).toBeVisible()

    // The password is accepted, but the app is closed until they enrol.
    await tryToSignIn(page, person.email, person.password)
    await expect(page).toHaveURL(/\/account\/2fa/)
    await page.goto('/inbox')
    await expect(page).toHaveURL(/\/account\/2fa/)

    // Nothing else to click: the page they are held on IS the setup.
    await expect(page.getByText('Your administrator requires two-factor authentication')).toBeVisible()
    const secret = (await page.locator('.secret-mono').innerText()).replace(/\s+/g, '')
    await page.getByLabel('Verify').fill(authenticatorCode(secret))
    await page.getByRole('button', { name: 'Enable two-factor' }).click()
    await expect(page.getByRole('heading', { name: 'Save these recovery codes.' })).toBeVisible()
    await page.getByLabel("I've saved them").check()
    await page.getByRole('button', { name: 'Continue' }).click()

    // Enrolled: the app opens - the bell too, which stayed silent until now -
    // and the next sign-in asks for the code.
    await page.getByRole('navigation', { name: 'Menu' }).getByRole('link', { name: 'Received' }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'Received' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Notifications' }).locator('.bell-badge')).toBeVisible()
    await signOut(page)
    await tryToSignIn(page, person.email, person.password)
    await page.getByLabel('Authentication code').fill(authenticatorCode(secret))
    await page.getByRole('button', { name: 'Continue' }).click()
    await expect(page).not.toHaveURL(/\/login(\?|$)/)
    await adminPage.context().close()
  })
})

test('U18 share approval switched on: a new share is held until an approver decides, then reaches its recipient', async ({
  page,
  browser,
}) => {
  await during('/api/admin/settings/share-approval', async () => {
    const sender = await given.user('employee', 'held')
    const recipient = await given.user('client', 'awaits')
    await given.connected(sender, recipient)
    const subject = `Held ${uid()}`

    const adminPage = await browserOf(browser, THE_ADMIN)
    await adminPage.goto('/admin/settings/share-approval')
    await adminPage.getByRole('checkbox', { name: 'Require approval for new shares' }).check()
    await adminPage.getByRole('radio', { name: /^Admins only/ }).check()
    await adminPage.getByRole('combobox', { name: 'Which shares need approval' }).selectOption('all')
    await adminPage.getByRole('button', { name: 'Save' }).click()
    await expect(toast(adminPage, 'Share-approval policy saved.')).toBeVisible()

    await signIn(page, sender)
    await page.goto('/share/new')
    await pickRecipient(page, recipient.name)
    await send(page, subject, 'draft.txt', 'the draft')
    await expect(page.getByText('All files sent')).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: 'View share' }).click()
    await expect(page.getByText('This share is awaiting approval.')).toBeVisible()

    // Not in the recipient's list in ANY state: the list opens on "Active",
    // where a held share would be missing whatever the server gave out.
    const theirs = await browserOf(browser, recipient)
    await expect(await receivedUnderAnyState(theirs, subject)).toHaveCount(0)

    await adminPage.goto('/approvals')
    await adminPage.locator('li.queue-row', { hasText: subject }).getByRole('link', { name: 'Review' }).click()
    await adminPage.getByRole('button', { name: 'Approve' }).click()
    await expect(adminPage.getByText('Share approved.')).toBeVisible()

    await theirs.goto('/inbox')
    await theirs.getByText(subject).click()
    expect((await download(theirs, theirs.getByRole('button', { name: 'Download', exact: true }))).text).toBe(
      'the draft',
    )
    await theirs.context().close()
    await adminPage.context().close()
  })
})

test.describe('U19 a policy decides what a person is offered', () => {
  test('public links for admins only, with one employee allowed', async ({ page, browser }) => {
    await during('/api/admin/settings/public-links/policy', async () => {
      const allowed = await given.user('employee', 'maylink')
      const denied = await given.user('employee', 'maynotlink')
      const adminPage = await browserOf(browser, THE_ADMIN)
      await adminPage.goto('/admin/settings/public-links')
      await adminPage.getByRole('radio', { name: /^Admins only/ }).check()
      await adminPage.getByRole('searchbox', { name: 'Search by name or email…' }).fill(allowed.email)
      await adminPage.locator('.user-suggest', { hasText: allowed.email }).click()
      await adminPage.locator('form.policy-form').getByRole('button', { name: 'Save' }).first().click()
      await expect(toast(adminPage, 'Public link policy saved.')).toBeVisible()

      const link = (p: Page) => p.getByRole('checkbox', { name: 'Also create a public download link' })
      await signIn(page, denied)
      await page.goto('/share/new')
      await expect(page.locator('.recipient-picker')).toBeVisible()
      await expect(link(page)).toHaveCount(0)

      const theirs = await browserOf(browser, allowed)
      await theirs.goto('/share/new')
      await expect(link(theirs)).toBeVisible()
      await theirs.context().close()
      await adminPage.context().close()
    })
  })

  test('API tokens for admins only, with one employee allowed', async ({ page, browser }) => {
    await during('/api/admin/settings/api-tokens/policy', async () => {
      const allowed = await given.user('employee', 'maytoken')
      const denied = await given.user('employee', 'maynottoken')
      const adminPage = await browserOf(browser, THE_ADMIN)
      await adminPage.goto('/admin/settings/api-tokens')
      await adminPage.getByRole('radio', { name: /^Admins only/ }).check()
      await adminPage.getByRole('searchbox', { name: 'Search by name or email…' }).fill(allowed.email)
      await adminPage.locator('.user-suggest', { hasText: allowed.email }).click()
      await adminPage.getByRole('button', { name: 'Save' }).click()
      await expect(toast(adminPage, 'API token policy saved.')).toBeVisible()

      await signIn(page, denied)
      await page.goto('/account')
      const panel = page.locator('#api-tokens')
      await expect(panel.getByText('Your administrator has restricted API token creation.')).toBeVisible()
      await expect(panel.getByRole('button', { name: 'Create token' })).toHaveCount(0)

      const theirs = await browserOf(browser, allowed)
      await theirs.goto('/account')
      await expect(theirs.locator('#api-tokens').getByRole('button', { name: 'Create token' })).toBeVisible()
      await theirs.context().close()
      await adminPage.context().close()
    })
  })

  test('secrets switched on, then off', async ({ page, browser }) => {
    await during('/api/admin/settings/secrets', async () => {
      const person = await given.user('employee', 'secretive')
      await signIn(page, person)
      const nav = page.getByRole('navigation', { name: 'Menu' })
      const adminPage = await browserOf(browser, THE_ADMIN)
      await adminPage.goto('/admin/settings/secrets')
      const on = adminPage.getByTestId('secrets-enabled')
      const save = adminPage
        .locator('form.policy-form > .actions, form.policy-form .actions')
        .first()
        .getByRole('button', { name: 'Save' })

      for (const enabled of [true, false]) {
        await on.setChecked(enabled)
        await save.click()
        await expect(toast(adminPage, 'Secrets policy saved.')).toBeVisible()
        await page.goto('/inbox')
        await expect(nav.getByRole('link', { name: 'Received' })).toBeVisible()
        await expect(nav.getByRole('link', { name: 'Secrets' })).toHaveCount(enabled ? 1 : 0)
      }
      await adminPage.context().close()
    })
  })

  test('changing one\'s own email allowed, then not', async ({ page, browser }) => {
    await during('/api/admin/settings/email-change', async () => {
      const person = await given.user('client', 'mayrename')
      await signIn(page, person)
      const adminPage = await browserOf(browser, THE_ADMIN)
      await adminPage.goto('/admin/settings/email-change')
      const self = adminPage.getByRole('checkbox', { name: 'Let users change their own email' })

      for (const allowed of [true, false]) {
        await self.setChecked(allowed)
        await adminPage.getByRole('button', { name: 'Save' }).click()
        await expect(toast(adminPage, 'Email-change policy saved.')).toBeVisible()
        await page.goto('/account')
        await expect(page.getByRole('heading', { level: 1, name: person.name })).toBeVisible()
        await expect(page.getByLabel('New email address')).toHaveCount(allowed ? 1 : 0)
      }
      await adminPage.context().close()
    })
  })
})

test('U20 maintenance mode: people see the banner and neither form will send; switched off, both do', async ({
  page,
  browser,
}) => {
  const person = await given.user('employee', 'duringmaint')
  const already = await given.user('employee', 'formopen')
  const colleague = await given.user('employee', 'aftermaint')
  const message = `Back at noon ${uid()}`
  // A share the person sent earlier, to add a file to.
  const existing = await given.share(person, { users: [colleague.id], files: { 'first.txt': 'first' } })
  await during('/api/admin/settings/maintenance', async () => {
    // Someone has the form open, filled in, before it starts.
    const openForm = await browserOf(browser, already)
    await openForm.goto('/share/new')
    await pickRecipient(openForm, colleague.name)

    const adminPage = await browserOf(browser, THE_ADMIN)
    await adminPage.goto('/admin/settings/maintenance')
    await adminPage.getByRole('checkbox', { name: 'Enable maintenance mode' }).check()
    await adminPage.getByRole('textbox', { name: /^Banner message/ }).fill(message)
    await adminPage.getByRole('button', { name: 'Save' }).click()
    await expect(toast(adminPage, 'Maintenance settings saved.')).toBeVisible()

    // Arriving now: the banner, and a form that says why it will not send.
    await signIn(page, person)
    await page.goto('/share/new')
    await expect(page.getByText(message).first()).toBeVisible()
    await pickRecipient(page, colleague.name)
    await page.locator('input.dropzone-input').setInputFiles(file('note.txt', 'a note'))
    await expect(page.getByTestId('submit-blockers')).toContainText('Maintenance mode is on: sending is paused.')
    await expect(page.getByRole('button', { name: 'Create + send' })).toBeDisabled()

    // Adding a file to a share they already sent: the same answer, before
    // anything is uploaded.
    await page.goto(`/share/${existing.id}`)
    await page.getByTestId('add-files-toggle').click()
    await page.locator('input.dropzone-input').setInputFiles(file('second.txt', 'second'))
    await expect(page.getByTestId('add-files-blocked')).toContainText('Maintenance mode is on: sending is paused.')
    await expect(page.getByTestId('add-files-submit')).toBeDisabled()

    // The form that was already open knows nothing of it: the server refuses
    // the file, and the form shows the reason.
    await send(openForm, `During maintenance ${uid()}`)
    await expect(
      openForm.getByText('The system is in maintenance mode. New transfers are paused; try again shortly.'),
    ).toBeVisible({ timeout: 30_000 })
    await expect(openForm.getByText('All files sent')).toHaveCount(0)
    expectedRefusal(/^503 POST .*\/api\/uploads\//)
    await openForm.context().close()

    await adminPage.getByRole('checkbox', { name: 'Enable maintenance mode' }).uncheck()
    await adminPage.getByRole('button', { name: 'Save' }).click()
    await expect(toast(adminPage, 'Maintenance settings saved.')).toBeVisible()

    await page.goto('/share/new')
    await expect(page.getByRole('heading', { level: 1, name: 'New share' })).toBeVisible()
    await expect(page.getByText(message)).toHaveCount(0)
    await pickRecipient(page, colleague.name)
    await send(page, `After maintenance ${uid()}`)
    await expect(page.getByText('All files sent')).toBeVisible({ timeout: 30_000 })

    await page.goto(`/share/${existing.id}`)
    await page.getByTestId('add-files-toggle').click()
    await page.locator('input.dropzone-input').setInputFiles(file('second.txt', 'second'))
    await expect(page.getByTestId('add-files-blocked')).toHaveCount(0)
    await page.getByTestId('add-files-submit').click()
    await expect(toast(page, 'Added 1 file to the share.')).toBeVisible({ timeout: 30_000 })
    await adminPage.context().close()
  })
})

test('U21 file preview switched off: a recipient is no longer offered Preview', async ({ page, browser }) => {
  await during('/api/admin/settings/file-preview', async () => {
    const sender = await given.user('employee', 'previews')
    const recipient = await given.user('client', 'looks')
    await given.connected(sender, recipient)
    const share = await given.share(sender, { users: [recipient.id], files: { 'readme.txt': 'read me' } })

    await signIn(page, recipient)
    const adminPage = await browserOf(browser, THE_ADMIN)
    await adminPage.goto('/admin/settings/transfers')
    const preview = adminPage.getByRole('checkbox', { name: 'Enable in-browser file preview' })
    const form = adminPage.locator('form', { has: preview })

    for (const enabled of [true, false]) {
      await preview.setChecked(enabled)
      await form.getByRole('button', { name: 'Save' }).click()
      await expect(toast(adminPage, 'File preview setting saved.')).toBeVisible()
      await page.goto(`/share/${share.id}`)
      await expect(page.getByRole('button', { name: 'Download', exact: true })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Preview' })).toHaveCount(enabled ? 1 : 0)
    }
    await adminPage.context().close()
  })
})

test('U21 a storage quota the admin sets on a person stops their next upload, with the reason', async ({
  page,
  browser,
}) => {
  const person = await given.user('employee', 'limited')
  const colleague = await given.user('employee', 'unlimited')
  const adminPage = await browserOf(browser, THE_ADMIN)
  await openUser(adminPage, person.email)
  await adminPage.getByLabel('Quota (bytes)').fill('10')
  await adminPage.getByRole('button', { name: 'Save' }).click()
  await expect(toast(adminPage, 'Saved.')).toBeVisible()
  await adminPage.reload()
  await expect(adminPage.getByLabel('Quota (bytes)')).toHaveValue('10')

  await signIn(page, person)
  await page.goto('/share/new')
  await pickRecipient(page, colleague.name)
  await send(page, `Over quota ${uid()}`, 'too-big.txt', 'x'.repeat(100))
  await expect(page.getByText('This upload would exceed your storage quota.', { exact: true })).toBeVisible({
    timeout: 30_000,
  })
  await adminPage.context().close()
})

// 1x1 transparent PNG.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
)

test('U22 a logo the admin enables for the sign-in page is what a visitor sees there', async ({
  page,
  browser,
}) => {
  const putBack = await given.brandingKept()
  try {
    await page.goto('/login')
    await expect(page.getByRole('heading', { level: 1, name: 'Welcome back.' })).toBeVisible()
    await expect(page.locator('.auth-brand img')).toHaveCount(0)

    const adminPage = await browserOf(browser, THE_ADMIN)
    await adminPage.goto('/admin/settings/branding')
    await adminPage.getByLabel('Upload logo').setInputFiles({ name: 'logo.png', mimeType: 'image/png', buffer: PNG })
    await expect(toast(adminPage, 'Logo uploaded.')).toBeVisible()
    await adminPage.getByRole('checkbox', { name: 'Login page' }).check()
    await adminPage.locator('.settings-section').first().getByRole('button', { name: 'Save' }).click()
    await expect(toast(adminPage, /saved/i)).toBeVisible()

    await page.reload()
    await expect(page.getByRole('heading', { level: 1, name: 'Welcome back.' })).toBeVisible()
    await expect(page.locator('.auth-brand img')).toBeVisible()
    await adminPage.context().close()
  } finally {
    await putBack()
  }
})
