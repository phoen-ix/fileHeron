import type { Page } from '@playwright/test'

import {
  THE_ADMIN,
  browserOf,
  confirm,
  download,
  expect,
  expectRefused,
  given,
  receivedUnderAnyState,
  signIn,
  test,
  uid,
} from './lib'

/* Who sees what (COVERAGE.c.md, V1-V7): a share, its files, the other people
 * it went to, the names of groups, and whom a person can find at all - each
 * read on the screen of someone who must NOT see it, beside someone who does.
 *
 * The routes behind these screens are asked directly, for every kind of
 * person and every state, in backend/tests/test_visibility_*.py. These are the
 * same rules as a person meets them: by address, in the lists with every state
 * shown, and in the search fields. */

const downloadButton = (page: Page) => page.getByRole('button', { name: 'Download', exact: true })

test('V1 a recipient sees neither the other recipients nor a group they are not in; the sender sees them all', async ({
  page,
  browser,
}) => {
  const sender = await given.user('employee', 'sends')
  const first = await given.user('client', 'firstrecipient')
  const second = await given.user('client', 'secondrecipient')
  const viaGroup = await given.user('client', 'viagroup')
  // Each client is connected to the sender on their own - never to each other.
  await given.connected(sender, first)
  await given.connected(sender, second)
  const groupName = `West region ${uid()}`
  const groupId = await given.group(groupName, [sender.id, viaGroup.id])
  const subject = `Price list ${uid()}`
  const share = await given.share(sender, {
    users: [first.id, second.id],
    groups: [groupId],
    subject,
    files: { 'prices.txt': 'the prices' },
  })

  await signIn(page, sender)
  await page.goto(`/share/${share.id}`)
  const people = page.getByTestId('recipient-people')
  await expect(people).toContainText(first.name)
  await expect(people).toContainText(second.name)
  await expect(page.locator('.group-chip', { hasText: groupName })).toBeVisible()

  // A direct recipient: the share, their file - and nobody else.
  const theirs = await browserOf(browser, first)
  await theirs.goto(`/share/${share.id}`)
  await expect(theirs.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  await expect(downloadButton(theirs)).toBeVisible()
  await expect(theirs.getByTestId('recipient-people')).toHaveCount(0)
  for (const hidden of [second.name, viaGroup.name, groupName]) {
    await expect(theirs.getByText(hidden)).toHaveCount(0)
  }
  const row = await receivedUnderAnyState(theirs, subject)
  await expect(row).toContainText(sender.name)
  for (const hidden of [second.name, viaGroup.name, groupName]) await expect(row).not.toContainText(hidden)

  // Someone who has it through the group: the group they are in, no person.
  const grouped = await browserOf(browser, viaGroup)
  await grouped.goto(`/share/${share.id}`)
  await expect(grouped.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  await expect(grouped.locator('.group-chip', { hasText: groupName })).toBeVisible()
  for (const hidden of [first.name, second.name]) await expect(grouped.getByText(hidden)).toHaveCount(0)
  for (const p of [theirs, grouped]) await p.context().close()
})

test('V2 a group member the admin removes can no longer open the share, by its address either', async ({
  page,
  browser,
}) => {
  const sender = await given.user('employee', 'togroup')
  const stays = await given.user('client', 'stays')
  const leaves = await given.user('client', 'leaves')
  const groupName = `Project ${uid()}`
  const groupId = await given.group(groupName, [sender.id, stays.id, leaves.id])
  const subject = `For the project ${uid()}`
  const share = await given.share(sender, { groups: [groupId], subject, files: { 'plan.txt': 'the plan' } })

  await signIn(page, leaves)
  await page.goto(`/share/${share.id}`)
  expect((await download(page, downloadButton(page))).text).toBe('the plan')

  const adminPage = await browserOf(browser, THE_ADMIN)
  await adminPage.goto(`/admin/groups/${groupId}`)
  const member = adminPage.locator('.member-row', { hasText: leaves.email })
  await member.getByRole('button', { name: 'Remove' }).click()
  await confirm(adminPage)
  await expect(member).toHaveCount(0)

  // The page they still have open offers a button; the server no longer serves it.
  await downloadButton(page).click()
  await expect(page.locator('.toast[data-tone="error"]').first()).toBeVisible()
  await expectRefused(page, share.id, subject, 'plan.txt')
  await expect(await receivedUnderAnyState(page, subject)).toHaveCount(0)

  // Whoever stayed is untouched.
  const still = await browserOf(browser, stays)
  await still.goto(`/share/${share.id}`)
  expect((await download(still, downloadButton(still))).text).toBe('the plan')
  await still.context().close()
  await adminPage.context().close()
})

test('V3 a share held for approval is nowhere for its recipient: not listed in any state, not by its address', async ({
  page,
  browser,
}) => {
  const policy = '/api/admin/settings/share-approval'
  const before = await given.settings(policy, {
    enabled: true,
    approver_mode: 'admins_only',
    scope: 'all',
    exempt_approvers: true,
    allow_content_review: true,
  })
  try {
    const sender = await given.user('employee', 'heldsender')
    const recipient = await given.user('client', 'heldrecipient')
    const colleague = await given.user('employee', 'noapprover')
    await given.connected(sender, recipient)
    const subject = `Held ${uid()}`
    const share = await given.share(sender, {
      users: [recipient.id],
      subject,
      files: { 'draft.txt': 'the draft' },
    })

    await signIn(page, sender)
    await page.goto(`/share/${share.id}`)
    await expect(page.getByText('This share is awaiting approval.')).toBeVisible()

    const theirs = await browserOf(browser, recipient)
    await expect(await receivedUnderAnyState(theirs, subject)).toHaveCount(0)
    await expectRefused(theirs, share.id, subject, 'draft.txt')

    // An employee who approves nothing has no business with it either.
    const other = await browserOf(browser, colleague)
    await expectRefused(other, share.id, subject, 'draft.txt')
    await other.goto('/approvals')
    await expect(other.getByText(subject)).toHaveCount(0)
    for (const p of [theirs, other]) await p.context().close()
  } finally {
    await given.settingsBack(policy, before)
  }
})

test('V4 a share is closed to an employee and to a client it was not sent to', async ({ page, browser }) => {
  const sender = await given.user('employee', 'private')
  const recipient = await given.user('client', 'privateto')
  const colleague = await given.user('employee', 'colleague')
  const stranger = await given.user('client', 'stranger')
  await given.connected(sender, recipient)
  const subject = `Private ${uid()}`
  const share = await given.share(sender, { users: [recipient.id], subject, files: { 'p.txt': 'private' } })

  await signIn(page, recipient)
  await page.goto(`/share/${share.id}`)
  await expect(downloadButton(page)).toBeVisible()

  for (const who of [colleague, stranger]) {
    const theirs = await browserOf(browser, who)
    await expectRefused(theirs, share.id, subject, 'p.txt')
    await expect(await receivedUnderAnyState(theirs, subject)).toHaveCount(0)
    await theirs.context().close()
  }
})

test("V5 what a client hands in: staff and the client's group see it and who sent it; a client outside does not", async ({
  page,
  browser,
}) => {
  const employee = await given.user('employee', 'staff')
  const submitter = await given.user('client', 'submits')
  const teammate = await given.user('client', 'teammate')
  const outsider = await given.user('client', 'outside')
  await given.group(`Client team ${uid()}`, [submitter.id, teammate.id])
  await given.group(`Other team ${uid()}`, [outsider.id])
  const subject = `Signed contract ${uid()}`
  const share = await given.submission(submitter, { subject, files: { 'contract.txt': 'signed' } })

  await signIn(page, employee)
  const staffRow = await receivedUnderAnyState(page, subject)
  await expect(staffRow).toContainText(submitter.name)
  await page.goto(`/share/${share.id}`)
  expect((await download(page, downloadButton(page))).text).toBe('signed')

  const mate = await browserOf(browser, teammate)
  await expect(await receivedUnderAnyState(mate, subject)).toContainText(submitter.name)
  await mate.goto(`/share/${share.id}`)
  expect((await download(mate, downloadButton(mate))).text).toBe('signed')

  const other = await browserOf(browser, outsider)
  await expect(await receivedUnderAnyState(other, subject)).toHaveCount(0)
  await expectRefused(other, share.id, subject, 'contract.txt')
  await expect(other.getByText(submitter.name)).toHaveCount(0)
  for (const p of [mate, other]) await p.context().close()
})

test('V6 a client finds the employee they work with, and no other client or employee', async ({ page }) => {
  const secrets = '/api/admin/settings/secrets'
  const before = await given.settings(secrets, { enabled: true })
  try {
    const theirEmployee = await given.user('employee', 'myemployee')
    const otherEmployee = await given.user('employee', 'notmine')
    const client = await given.user('client', 'searching')
    const otherClient = await given.user('client', 'otherclient')
    await given.connected(theirEmployee, client)
    await given.connected(otherEmployee, otherClient)

    await signIn(page, client)
    await page.goto('/secrets/new')
    const box = page.locator('.recipient-picker').getByRole('combobox')
    const found = async (name: string): Promise<boolean> => {
      await box.fill(name)
      const option = page.getByRole('option', { name })
      await expect(option.or(page.locator('.results-empty')).first()).toBeVisible()
      const yes = await option.isVisible()
      await box.fill('')
      return yes
    }

    expect({
      'their employee': await found(theirEmployee.name),
      'another employee': await found(otherEmployee.name),
      'another client': await found(otherClient.name),
      'another client, by address': await found(otherClient.email),
    }).toEqual({
      'their employee': true,
      'another employee': false,
      'another client': false,
      'another client, by address': false,
    })
  } finally {
    await given.settingsBack(secrets, before)
  }
})

test('V7 the Received filter "Via group" offers the groups I am in, and narrows the list to one', async ({ page }) => {
  const employee = await given.user('employee', 'filters')
  const sender = await given.user('employee', 'sendsboth')
  const mine = `My project ${uid()}`
  const notMine = `Their project ${uid()}`
  const mineId = await given.group(mine, [employee.id, sender.id])
  await given.group(notMine, [sender.id])
  const viaGroup = `To the group ${uid()}`
  const direct = `To me alone ${uid()}`
  await given.share(sender, { groups: [mineId], subject: viaGroup, files: { 'g.txt': 'g' } })
  await given.share(sender, { users: [employee.id], subject: direct, files: { 'd.txt': 'd' } })

  await signIn(page, employee)
  await page.goto('/inbox')
  await expect(page.getByText(viaGroup)).toBeVisible()
  await expect(page.getByText(direct)).toBeVisible()

  await page.getByRole('combobox', { name: 'Any sender' }).selectOption({ label: 'Via group' })
  const groups = page.getByRole('combobox', { name: 'Filter' })
  // It was empty for everyone but an admin: it asked for the admin's list.
  await expect(groups.getByRole('option', { name: mine })).toBeAttached()
  await expect(groups.getByRole('option', { name: notMine })).toHaveCount(0)
  await groups.selectOption({ label: mine })
  await expect(page.getByText(viaGroup)).toBeVisible()
  await expect(page.getByText(direct)).toHaveCount(0)
})
