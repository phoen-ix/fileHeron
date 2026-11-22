import type { Page } from '@playwright/test'

import {
  ANNOUNCED_WITHIN,
  THE_ADMIN,
  browserOf,
  confirm,
  download,
  expect,
  file,
  given,
  mailed,
  pathOf,
  pickRecipient,
  receivedUnderAnyState,
  signIn,
  test,
  toast,
  uid,
} from './lib'

/* Sharing, from the sender's hand to the recipient's (COVERAGE.c.md,
 * U11-U16): who can be addressed after an admin changed a group, a share
 * opened from its mail and from the bell, files a client hands in, a link
 * mailed to someone without an account, and a file the scanner stopped. */

/** Whether `name` can be addressed on the new-share form the page is on. */
async function offered(page: Page, name: string): Promise<boolean> {
  const box = page.locator('.recipient-picker').getByRole('combobox')
  await box.fill(name)
  const option = page.getByRole('option', { name })
  const none = page.locator('.results-empty')
  await expect(option.or(none).first()).toBeVisible()
  const yes = await option.isVisible()
  await box.fill('')
  return yes
}

async function send(page: Page, subject: string, fileName: string, text: string): Promise<void> {
  await page.locator('input.dropzone-input').setInputFiles(file(fileName, text))
  await page.getByLabel('Subject', { exact: true }).fill(subject)
  await page.getByRole('button', { name: 'Create + send' }).click()
  await expect(page.getByText('All files sent')).toBeVisible({ timeout: 30_000 })
}

test('U11 a client the admin adds to a group can be sent to, and loses the group\'s shares when removed', async ({
  page,
  browser,
}) => {
  const employee = await given.user('employee', 'grouped')
  const client = await given.user('client', 'joining')
  const groupName = `Project ${uid()}`
  await given.group(groupName, [employee.id])
  const subject = `For the project ${uid()}`

  await signIn(page, employee)
  await page.goto('/share/new')
  expect(await offered(page, client.name)).toBe(false)

  const adminPage = await browserOf(browser, THE_ADMIN)
  await adminPage.goto('/admin/groups')
  await adminPage.locator('.group-table tbody tr', { hasText: groupName }).click()
  await expect(adminPage).toHaveURL(/\/admin\/groups\/\d+$/)
  await adminPage.getByRole('textbox', { name: 'Search to add a member…' }).fill(client.email)
  await adminPage.locator('.add-result', { hasText: client.email }).getByRole('button', { name: 'Add' }).click()
  const member = adminPage.locator('.member-row', { hasText: client.email })
  await expect(member).toBeVisible()

  // The employee can address the client now, and sends to the group.
  await page.reload()
  expect(await offered(page, client.name)).toBe(true)
  await pickRecipient(page, groupName)
  await send(page, subject, 'plan.txt', 'the plan')

  const theirs = await browserOf(browser, client)
  await theirs.goto('/inbox')
  await theirs.getByText(subject).click()
  expect(await download(theirs, theirs.getByRole('button', { name: 'Download', exact: true }))).toEqual({
    name: 'plan.txt',
    text: 'the plan',
  })

  // Out of the group: the share is no longer theirs, and they cannot be addressed.
  await member.getByRole('button', { name: 'Remove' }).click()
  await confirm(adminPage)
  await expect(member).toHaveCount(0)
  await theirs.goto('/inbox')
  await expect(theirs.getByRole('heading', { level: 1, name: 'Received' })).toBeVisible()
  await expect(theirs.getByText(subject)).toHaveCount(0)
  await page.goto('/share/new')
  expect(await offered(page, client.name)).toBe(false)
  await theirs.context().close()
  await adminPage.context().close()
})

test('U12 a share is opened from its mail and from the bell, downloaded, and the sender sees it was', async ({
  page,
  browser,
}) => {
  test.setTimeout(180_000)
  const sender = await given.user('employee', 'mailer')
  const byMail = await given.user('client', 'bymail')
  const byBell = await given.user('client', 'bybell')
  await given.connected(sender, byMail, byBell)
  const subject = `Opened twice ${uid()}`

  // The second recipient is already in the app when it arrives.
  const bellPage = await browserOf(browser, byBell)
  await bellPage.goto('/inbox')

  await signIn(page, sender)
  await page.goto('/share/new')
  await pickRecipient(page, byMail.name)
  await pickRecipient(page, byBell.name)
  await page.getByLabel('Download limit (optional)').fill('5')
  await send(page, subject, 'minutes.txt', 'the minutes')
  await page.getByRole('button', { name: 'View share' }).click()
  // The sender's page names who it went to.
  const people = page.getByTestId('recipient-people')
  await expect(people).toContainText(byMail.name)
  await expect(people).toContainText(byBell.name)
  await expect(page.getByText('0 of 5')).toBeVisible()

  // From the mail: the link asks them to sign in, then opens the share.
  const link = (await mailed(byMail.email, /https?:\/\/\S+\/share\/[0-9a-f-]{36}/, ANNOUNCED_WITHIN))[0]
  const mailPage = await browserOf(browser)
  await mailPage.goto(pathOf(link))
  await expect(mailPage).toHaveURL(/\/login\?redirect=/)
  await mailPage.getByLabel('Email', { exact: true }).fill(byMail.email)
  await mailPage.getByLabel('Password', { exact: true }).fill(byMail.password)
  await mailPage.getByRole('button', { name: 'Continue' }).click()
  await expect(mailPage.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  expect(
    (await download(mailPage, mailPage.getByRole('button', { name: 'Download', exact: true }))).text,
  ).toBe('the minutes')

  // From the bell, without a reload.
  const bell = bellPage.getByRole('button', { name: 'Notifications' })
  await bell.click()
  await bellPage
    .getByRole('region', { name: 'Notifications' })
    .getByText(`${sender.name} sent you 1 file.`)
    .click()
  await expect(bellPage.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  expect(
    (await download(bellPage, bellPage.getByRole('button', { name: 'Download', exact: true }))).text,
  ).toBe('the minutes')

  // The sender sees both downloads.
  await page.reload()
  await expect(page.getByText('2 of 5')).toBeVisible()
  await mailPage.context().close()
  await bellPage.context().close()
})

test('U13 files a client hands in are downloaded by an employee from Received', async ({ page, browser }) => {
  const client = await given.user('client', 'handsin')
  const employee = await given.user('employee', 'receives')
  const subject = `Handed in ${uid()}`
  await signIn(page, client)
  await page.goto('/share/new')
  await expect(page.locator('.recipient-picker')).toHaveCount(0)
  await send(page, subject, 'signed.txt', 'signed and sealed')

  const staff = await browserOf(browser, employee)
  await staff.goto('/inbox')
  await staff.getByText(subject).click()
  await expect(staff.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  expect(await download(staff, staff.getByRole('button', { name: 'Download', exact: true }))).toEqual({
    name: 'signed.txt',
    text: 'signed and sealed',
  })
  await staff.context().close()
})

test('U14 someone without an account opens the mailed link, unlocks it, and downloads up to the limit', async ({
  page,
  browser,
}) => {
  test.setTimeout(180_000)
  const policy = '/api/admin/settings/public-links/policy'
  const before = await given.settings(policy, { external_recipients_enabled: true })
  try {
    const sender = await given.user('employee', 'tooutside')
    const outsider = `outside-${uid()}@e2e.example`
    const subject = `For outside ${uid()}`
    await signIn(page, sender)
    await page.goto('/share/new')
    await pickRecipient(page, outsider)
    const link = page.locator('.public-link-fields')
    await link.getByLabel('Password (optional)').fill('open-sesame-42')
    await link.getByLabel('Download limit (optional)').fill('2')
    await link.getByLabel('Notify me on every download').check()
    await send(page, subject, 'offer.txt', 'the offer')

    // The link in THEIR mail.
    const mailedLink = (await mailed(outsider, /https?:\/\/\S+\/d\/[A-Za-z0-9_-]+/, ANNOUNCED_WITHIN))[0]
    const guest = await browserOf(browser)
    await guest.goto(pathOf(mailedLink))
    await guest.getByLabel('Password').fill('not-the-password')
    await guest.getByRole('button', { name: 'Unlock' }).click()
    await expect(guest.getByRole('alert')).toBeVisible()
    await guest.getByLabel('Password').fill('open-sesame-42')
    await guest.getByRole('button', { name: 'Unlock' }).click()
    await expect(guest.getByRole('heading', { level: 1, name: subject })).toBeVisible()

    const row = guest.locator('.file-row', { hasText: 'offer.txt' })
    for (const left of ['1 download left', '0 downloads left']) {
      expect((await download(guest, row.getByRole('button', { name: 'Download' }))).text).toBe('the offer')
      await expect(guest.getByText(left).or(guest.getByText('reached its download limit'))).toBeVisible()
    }
    // Used up: a fresh visit is told so.
    await guest.goto(pathOf(mailedLink))
    await expect(guest.getByText('This link has reached its download limit.')).toBeVisible()

    // The sender was told, in the bell and by mail.
    await page.goto('/outbox')
    await page.getByRole('button', { name: 'Notifications' }).click()
    await expect(page.getByText('Public link downloaded: offer.txt').first()).toBeVisible()
    await mailed(sender.email, /^Subject:.*download/im)
    await guest.context().close()
  } finally {
    await given.settingsBack(policy, before)
  }
})

test('U15 a file the scanner stops: the sender is told, the recipient gets it once the admin releases it, and the sender is told that too', async ({
  page,
  browser,
}) => {
  const sender = await given.user('employee', 'flagged')
  const recipient = await given.user('client', 'waits')
  await given.connected(sender, recipient)
  const subject = `Flagged ${uid()}`
  const fileName = `flagged-${uid()}.txt`
  const share = await given.infectedShare(sender, { users: [recipient.id], subject, fileName })

  // The sender's bell says which file.
  await signIn(page, sender)
  await page.getByRole('button', { name: 'Notifications' }).click()
  await expect(page.getByText(`${fileName} was quarantined by antivirus.`)).toBeVisible()

  // The recipient has nothing to download: the share is off the list of what
  // they can use, and under "Any state" it is there as revoked, without its file.
  const theirs = await browserOf(browser, recipient)
  await theirs.goto('/inbox')
  await expect(theirs.getByRole('heading', { level: 1, name: 'Received' })).toBeVisible()
  await expect(theirs.getByText('Loading…')).toHaveCount(0)
  await expect(theirs.getByText(subject)).toHaveCount(0)
  await expect(await receivedUnderAnyState(theirs, subject)).toContainText(/revoked/i)
  await theirs.goto(`/share/${share.id}`)
  await expect(theirs.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  await expect(theirs.getByRole('button', { name: 'Download', exact: true })).toHaveCount(0)

  const adminPage = await browserOf(browser, THE_ADMIN)
  await adminPage.goto('/admin/quarantine')
  await adminPage.getByRole('searchbox').fill(fileName)
  await adminPage.locator('tbody tr', { hasText: fileName }).getByRole('button', { name: 'Release', exact: true }).click()
  const box = adminPage.locator('.confirm-backdrop')
  await box.locator('textarea').fill('A false positive, checked by hand.')
  await box.getByRole('button', { name: 'Release', exact: true }).click()
  await expect(adminPage.locator('tbody tr', { hasText: fileName })).toHaveCount(0)

  // Released: the share is back and the file downloads.
  await theirs.goto(`/share/${share.id}`)
  await expect(theirs.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  expect((await download(theirs, theirs.getByRole('button', { name: 'Download', exact: true }))).name).toBe(fileName)

  // The sender, who was told it was stopped, is told it was released - in the
  // bell (its entry opens the share) and by mail.
  await page.reload()
  await page.getByRole('button', { name: 'Notifications' }).click()
  await page.getByText(`${fileName} was released from quarantine.`).click()
  await expect(page.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  await mailed(sender.email, /^Subject:.*quarantined file was released/im)
  await theirs.context().close()
  await adminPage.context().close()
})

test('U15 a file the scanner stops and the admin purges is gone for good: the sender sees an empty share, the recipient none', async ({
  page,
  browser,
}) => {
  const sender = await given.user('employee', 'purged')
  const recipient = await given.user('client', 'never')
  await given.connected(sender, recipient)
  const subject = `Purged ${uid()}`
  const fileName = `malware-${uid()}.txt`
  const share = await given.infectedShare(sender, { users: [recipient.id], subject, fileName })

  const adminPage = await browserOf(browser, THE_ADMIN)
  await adminPage.goto('/admin/quarantine')
  await adminPage.getByRole('searchbox').fill(fileName)
  await adminPage.locator('tbody tr', { hasText: fileName }).getByRole('button', { name: 'Purge', exact: true }).click()
  await adminPage.locator('.confirm-backdrop').getByRole('button', { name: 'Purge', exact: true }).click()
  await expect(toast(adminPage, 'File bytes purged from disk.')).toBeVisible()

  // The sender is told it is gone for good, in the bell and by mail ...
  await signIn(page, sender)
  await page.getByRole('button', { name: 'Notifications' }).click()
  await expect(page.getByText(`${fileName} was removed after the antivirus flagged it.`)).toBeVisible()
  await mailed(sender.email, /^Subject:.*quarantined file was removed/im)
  // ... and still has the share's page: revoked, and with nothing in it.
  await page.goto(`/share/${share.id}`)
  await expect(page.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  await expect(page.getByText('No files in this share.')).toBeVisible()
  await expect(page.getByText(fileName)).toHaveCount(0)

  // The recipient has nothing to download, however they look for it.
  const theirs = await browserOf(browser, recipient)
  await theirs.goto('/inbox')
  await expect(theirs.getByRole('heading', { level: 1, name: 'Received' })).toBeVisible()
  await expect(theirs.getByText('Loading…')).toHaveCount(0)
  await expect(theirs.getByText(subject)).toHaveCount(0)
  await expect(await receivedUnderAnyState(theirs, subject)).toContainText(/revoked/i)
  await theirs.goto(`/share/${share.id}`)
  await expect(theirs.getByText('No files in this share.')).toBeVisible()
  await expect(theirs.getByText(fileName)).toHaveCount(0)
  await theirs.context().close()
  await adminPage.context().close()
})

test('U16 a group the admin marks as a company inbox can be addressed by an employee outside it', async ({
  page,
  browser,
}) => {
  const employee = await given.user('employee', 'outsider')
  const s = uid()
  const plain = `Closed circle ${s}`
  const inbox = `Front desk ${s}`

  const adminPage = await browserOf(browser, THE_ADMIN)
  for (const [name, asInbox] of [
    [plain, false],
    [inbox, true],
  ] as const) {
    await adminPage.goto('/admin/groups')
    await adminPage.getByRole('button', { name: /new group/i }).click()
    await adminPage.getByRole('textbox', { name: 'Name' }).fill(name)
    if (asInbox) await adminPage.getByRole('checkbox', { name: /mark as company inbox/i }).check()
    await adminPage.getByRole('button', { name: 'Create', exact: true }).click()
    await expect(adminPage.locator('.group-table tbody tr', { hasText: name })).toBeVisible()
  }

  await signIn(page, employee)
  await page.goto('/share/new')
  expect(await offered(page, plain)).toBe(false)
  expect(await offered(page, inbox)).toBe(true)
  await pickRecipient(page, inbox)
  await send(page, `To the front desk ${s}`, 'note.txt', 'a note')
  await page.getByRole('button', { name: 'View share' }).click()
  await expect(page.locator('.group-chip', { hasText: inbox })).toBeVisible()
  await adminPage.context().close()
})
