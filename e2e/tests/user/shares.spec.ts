import type { Locator, Page } from '@playwright/test'

import { apiFetch } from '../../helpers'
import {
  admin,
  connect,
  createShare,
  expect,
  group,
  newUser,
  ok,
  restoreSettings,
  session,
  setSettings,
  signIn,
  test,
  tokenOf,
  uid,
  upload,
} from './lib'

/* Shares from both ends: composing one in the UI (files, recipients, groups,
 * expiry), the blockers that keep Send disabled, a client's share to the
 * company, the outbox filters and bulk expiry, everything on the share page
 * (expiry, download limit, preview, delete, ZIP, end), and the approval
 * round trip (held, rejected, resubmitted, approved, discarded). */

const confirmDialog = (page: Page) => page.getByRole('dialog').getByRole('button').last()

/** Pick a recipient: type, open the list with the keyboard, take the option.
 * The list only opens on focus or arrow keys, so a second pick in a still
 * focused field needs the arrow. */
async function pick(page: Page, query: string, option: string | RegExp): Promise<void> {
  const box = page.locator('.recipient-picker').getByRole('combobox')
  await box.fill(query)
  const opt = page.getByRole('option', { name: option })
  await expect(async () => {
    if (!(await opt.isVisible())) await box.press('ArrowDown')
    await expect(opt).toBeVisible({ timeout: 1_000 })
  }).toPass()
  await opt.click()
}

const file = (name: string, text: string) => ({ name, mimeType: 'text/plain', buffer: Buffer.from(text) })

async function download(page: Page, trigger: Locator): Promise<{ name: string; text: string }> {
  const [dl] = await Promise.all([page.waitForEvent('download'), trigger.click()])
  const stream = await dl.createReadStream()
  const chunks: Buffer[] = []
  for await (const c of stream) chunks.push(c as Buffer)
  return { name: dl.suggestedFilename(), text: Buffer.concat(chunks).toString('latin1') }
}

test('an employee composes a share for a user and a group; the recipients download it', async ({
  page,
  browser,
}) => {
  const sender = await newUser('employee', 'composer')
  const direct = await newUser('client', 'direct')
  const member = await newUser('client', 'member')
  const groupName = `E2E group ${uid()}`
  await group(groupName, [member.id, sender.id])
  await connect(sender, direct)
  const subject = `Composed ${uid()}`

  await signIn(page, sender)
  await page.goto('/share/new')
  await page.locator('input.dropzone-input').setInputFiles(file('report.txt', 'quarterly numbers'))
  await page.getByLabel('Subject', { exact: true }).fill(subject)
  await page.getByLabel('Message', { exact: true }).fill('Here it is.')
  await pick(page, direct.name, new RegExp(direct.name))
  await pick(page, groupName, new RegExp(groupName))
  await expect(page.locator('.recipient-picker .chip')).toHaveCount(2)
  await page.getByRole('button', { name: '1 day' }).click()
  await page.getByLabel('Download limit (optional)').fill('10')
  await page.getByRole('button', { name: 'Create + send' }).click()

  await expect(page.getByText('All files sent')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'View share' }).click()
  await expect(page.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Files (1)' })).toBeVisible()
  await expect(page.getByText(groupName)).toBeVisible()
  await expect(page.getByText('0 of 10')).toBeVisible()

  for (const who of [direct, member]) {
    const r = await session(browser, who)
    await r.goto('/inbox')
    await r.getByText(subject).click()
    await expect(r.getByRole('heading', { level: 1, name: subject })).toBeVisible()
    await expect(r.getByText('Here it is.')).toBeVisible()
    // A recipient reads and downloads; managing the share is the sender's.
    for (const owners of ['Edit', 'End share', 'Delete', '+ Add files']) {
      await expect(r.getByRole('button', { name: owners, exact: true })).toHaveCount(0)
    }
    const got = await download(r, r.getByRole('button', { name: 'Download', exact: true }))
    expect(got).toEqual({ name: 'report.txt', text: 'quarterly numbers' })
    await r.context().close()
  }
})

test('Send stays disabled while the blockers list says why', async ({ page }) => {
  const sender = await newUser('employee', 'blockers')
  await signIn(page, sender)
  await page.goto('/share/new')
  const send = page.getByRole('button', { name: 'Create + send' })
  const blockers = page.getByTestId('submit-blockers')
  await expect(send).toBeDisabled()
  await expect(blockers).toContainText('Add at least one file.')
  await expect(blockers).toContainText('Pick a recipient from the list, or attach a public link below.')

  await page.locator('input.dropzone-input').setInputFiles(file('a.txt', 'a'))
  await expect(blockers).not.toContainText('Add at least one file.')
  await page.locator('.recipient-picker').getByRole('combobox').fill('typed-not-picked')
  await page.getByLabel('Subject', { exact: true }).click()
  await expect(blockers).toContainText('“typed-not-picked” is typed in Recipients but not picked.')
  await page.getByTestId('recipient-clear').click()
  await page.getByLabel('Also create a public download link').check()
  await expect(blockers).toHaveCount(0)
  await expect(send).toBeEnabled()
})

test('a client sends files to the company; employees receive them', async ({ page, browser }) => {
  const client = await newUser('client', 'inbound')
  const employee = await newUser('employee', 'staff')
  const subject = `To the company ${uid()}`
  await signIn(page, client)
  await page.goto('/share/new')
  await expect(page.getByText('This goes to the company')).toBeVisible()
  await expect(page.locator('.recipient-picker')).toHaveCount(0)
  await page.locator('input.dropzone-input').setInputFiles(file('signed.txt', 'signed'))
  await page.getByLabel('Subject', { exact: true }).fill(subject)
  await page.getByRole('button', { name: 'Create + send' }).click()
  await expect(page.getByText('All files sent')).toBeVisible({ timeout: 30_000 })

  const staff = await session(browser, employee)
  await staff.goto('/inbox')
  await staff.getByText(subject).click()
  await expect(staff.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  await expect(staff.getByText(/^Inbound · ID/)).toBeVisible()
  await staff.context().close()
})

test('outbox: search, state filter, and bulk expiry', async ({ page }) => {
  const sender = await newUser('employee', 'outbox')
  const to = await newUser('client', 'outboxto')
  await connect(sender, to)
  const token = await tokenOf(sender)
  const tag = uid()
  const a = `Alpha ${tag}`
  const b = `Bravo ${tag}`
  const c = `Charlie ${tag}`
  for (const s of [a, b, c]) await createShare(token, { users: [to.id], subject: s, files: { 'x.txt': s } })
  const revoked = await createShare(token, { users: [to.id], subject: `Revoked ${tag}`, files: { 'r.txt': 'r' } })
  await ok(apiFetch(token, `/api/shares/${revoked.id}`, { method: 'DELETE' }), 'revoke')

  await signIn(page, sender)
  await page.goto('/outbox')
  const rows = page.locator('tbody tr')
  await expect(rows).toHaveCount(3)
  await page.getByPlaceholder('Search by subject…').fill('Bravo')
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText(b)
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await page.getByRole('combobox', { name: 'Any state' }).selectOption('revoked')
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText(`Revoked ${tag}`)
  await page.getByRole('button', { name: 'Clear filters' }).click()

  await expect(rows).toHaveCount(3)
  await rows.filter({ hasText: a }).getByRole('checkbox').check()
  await rows.filter({ hasText: c }).getByRole('checkbox').check()
  const bar = page.getByRole('region', { name: 'Bulk actions' })
  await expect(bar).toContainText('2 selected')
  await bar.getByRole('button', { name: 'Expire now' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Expire now' }).click()
  await expect(page.getByText('2 shares expired.')).toBeVisible()
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText(b)
})

test('outbox: sort by subject, group by recipient, filter by recipient', async ({ page }) => {
  const sender = await newUser('employee', 'sorter')
  const ann = await newUser('client', 'ann')
  const bob = await newUser('client', 'bob')
  await connect(sender, ann, bob)
  const token = await tokenOf(sender)
  const tag = uid()
  await createShare(token, { users: [ann.id], subject: `B ${tag}`, files: { 'b.txt': 'b' } })
  await createShare(token, { users: [bob.id], subject: `A ${tag}`, files: { 'a.txt': 'a' } })
  await createShare(token, { users: [ann.id], subject: `C ${tag}`, files: { 'c.txt': 'c' } })

  await signIn(page, sender)
  await page.goto('/outbox')
  const header = page.getByRole('columnheader', { name: 'Subject' })
  await header.getByRole('button').click()
  await expect(header).toHaveAttribute('aria-sort', 'ascending')
  await expect(page.locator('tbody tr').first()).toContainText(`A ${tag}`)
  await header.getByRole('button').click()
  await expect(header).toHaveAttribute('aria-sort', 'descending')
  await expect(page.locator('tbody tr').first()).toContainText(`C ${tag}`)

  await page.getByLabel('Group by').selectOption({ label: 'Recipient (user)' })
  await expect(page.getByRole('heading', { level: 2, name: new RegExp(ann.name) })).toBeVisible()
  await expect(page.getByRole('heading', { level: 2, name: new RegExp(bob.name) })).toBeVisible()
  await page.getByLabel('Group by').selectOption({ label: 'None' })

  await page.getByRole('combobox', { name: 'Any recipient' }).selectOption('user')
  await page.getByPlaceholder('Search by name or email…').fill(bob.name)
  await page.getByRole('button', { name: new RegExp(bob.name) }).click()
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await expect(page.locator('tbody tr').first()).toContainText(`A ${tag}`)
})

test("someone else's share is refused, not shown", async ({ page }) => {
  const owner = await newUser('employee', 'private')
  const to = await newUser('client', 'privateto')
  await connect(owner, to)
  const share = await createShare(await tokenOf(owner), {
    users: [to.id],
    subject: `Private ${uid()}`,
    files: { 'p.txt': 'p' },
  })
  await signIn(page, await newUser('client', 'outsider'))
  await page.goto(`/share/${share.id}`)
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByText('p.txt')).toHaveCount(0)
})

test('share page: limit, expiry, preview, delete a file, ZIP, end the share', async ({ page }) => {
  const owner = await newUser('employee', 'detail')
  const to = await newUser('client', 'detailto')
  await connect(owner, to)
  const subject = `Detail ${uid()}`
  const share = await createShare(await tokenOf(owner), {
    users: [to.id],
    subject,
    files: { 'keep.txt': 'keep me', 'drop.txt': 'drop me' },
  })
  await signIn(page, owner)
  await page.goto(`/share/${share.id}`)
  await expect(page.getByRole('heading', { level: 1, name: subject })).toBeVisible()

  // Download limit.
  await page.locator('.fh-kv', { hasText: 'Downloads' }).getByRole('button', { name: 'Edit' }).click()
  await page.getByLabel('New limit').fill('5')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Download limit updated.')).toBeVisible()
  await expect(page.getByText('0 of 5')).toBeVisible()

  // Expiry.
  await page.locator('.fh-kv', { hasText: 'Expires' }).getByRole('button', { name: 'Edit' }).click()
  await page.getByRole('button', { name: '30 days' }).click()
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Expiry updated.')).toBeVisible()

  // Preview a text file in the modal.
  const keep = page.locator('.file-row', { hasText: 'keep.txt' })
  await keep.getByRole('button', { name: 'Preview' }).click()
  const modal = page.getByRole('dialog')
  await expect(modal).toContainText('keep me')
  await modal.getByRole('button', { name: /Close/ }).click()
  await expect(modal).toHaveCount(0)

  // Delete one file.
  await page.locator('.file-row', { hasText: 'drop.txt' }).getByRole('button', { name: 'Delete' }).click()
  await confirmDialog(page).click()
  await expect(page.getByText('File deleted.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Files (1)' })).toBeVisible()

  // The ZIP of what is left.
  const zip = await download(page, page.getByRole('button', { name: 'Download all as ZIP' }))
  expect(zip.name).toMatch(/\.zip$/)
  expect(zip.text).toContain('keep.txt')
  expect(zip.text).not.toContain('drop.txt')

  // End it.
  await page.getByRole('button', { name: 'End share' }).click()
  await confirmDialog(page).click()
  await expect(page.getByText('Share ended and files deleted.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Files (0)' })).toBeVisible()
})

test.describe.serial('share approval', () => {
  let before: Record<string, unknown>
  test.beforeAll(async () => {
    before = await setSettings('/api/admin/settings/share-approval', {
      enabled: true,
      approver_mode: 'admins_only',
      scope: 'outbound',
      exempt_approvers: true,
      allow_content_review: true,
    })
  })
  test.afterAll(async () => restoreSettings('/api/admin/settings/share-approval', before))

  test('held, rejected with a reason, resubmitted, approved - then the recipient sees it', async ({
    page,
    browser,
  }) => {
    test.setTimeout(90_000)
    const sender = await newUser('employee', 'held')
    const to = await newUser('client', 'heldto')
    const approver = await newUser('admin', 'approver')
    await connect(sender, to)
    const subject = `Held ${uid()}`
    const share = await createShare(await tokenOf(sender), { users: [to.id], subject, files: { 'h.txt': 'held' } })

    await signIn(page, sender)
    await page.goto(`/share/${share.id}`)
    await expect(page.getByText('This share is awaiting approval.')).toBeVisible()
    const recipient = await session(browser, to)
    await recipient.goto('/inbox')
    await expect(recipient.getByText(subject)).toHaveCount(0)

    const boss = await session(browser, approver)
    await boss.goto('/approvals')
    const row = boss.locator('li.queue-row', { hasText: subject })
    await row.getByRole('link', { name: 'Review' }).click()
    await expect(boss.getByRole('heading', { level: 1, name: subject })).toBeVisible()
    await boss.getByRole('button', { name: 'Reject', exact: true }).click()
    await boss.getByLabel('Reason').fill('Wrong recipient')
    await boss.getByRole('button', { name: 'Reject share' }).click()
    await expect(boss.getByText('Share rejected.')).toBeVisible()

    await page.reload()
    await expect(page.getByText('This share was not approved.')).toBeVisible()
    await expect(page.getByText('Wrong recipient')).toBeVisible()
    await page.getByRole('button', { name: 'Resubmit for approval' }).click()
    await expect(page.getByText('Share resubmitted for approval.')).toBeVisible()

    await boss.goto('/approvals')
    await boss.locator('li.queue-row', { hasText: subject }).getByRole('link', { name: 'Review' }).click()
    await boss.getByRole('button', { name: 'Approve' }).click()
    await expect(boss.getByText('Share approved.')).toBeVisible()
    await boss.goto('/approvals')
    await expect(boss.getByText(subject)).toHaveCount(0)

    await recipient.goto('/inbox')
    await expect(recipient.getByText(subject)).toBeVisible()

    // A file added after the approval waits for review before recipients get it.
    const token = await tokenOf(sender)
    const added = await upload(token, share.id, 'late.txt', 'late')
    await ok(
      apiFetch(token, `/api/shares/${share.id}/files-added`, {
        method: 'POST',
        body: JSON.stringify({ notify: false, file_ids: [added] }),
      }),
      'announce the added file',
    )
    await boss.goto(`/share/${share.id}`)
    await expect(boss.getByText(/1 file was added after this share was approved/)).toBeVisible()
    await boss.getByRole('button', { name: 'Release added files' }).click()
    await expect(boss.getByText('Added files released to recipients.')).toBeVisible()
    await recipient.goto(`/share/${share.id}`)
    await expect(recipient.getByText('late.txt')).toBeVisible()

    // The next one is discarded instead: the recipient never gets it.
    const nope = await upload(token, share.id, 'nope.txt', 'nope')
    await ok(
      apiFetch(token, `/api/shares/${share.id}/files-added`, {
        method: 'POST',
        body: JSON.stringify({ notify: false, file_ids: [nope] }),
      }),
      'announce the second added file',
    )
    await boss.goto(`/share/${share.id}`)
    await boss.getByRole('button', { name: 'Discard added files' }).click()
    await expect(boss.getByText('Added files discarded.')).toBeVisible()
    await recipient.goto(`/share/${share.id}`)
    await expect(recipient.getByText('late.txt')).toBeVisible()
    await expect(recipient.getByText('nope.txt')).toHaveCount(0)
    for (const p of [recipient, boss]) await p.context().close()
  })

  test('the sender discards a held share', async ({ page }) => {
    const sender = await newUser('employee', 'discard')
    const to = await newUser('client', 'discardto')
    await connect(sender, to)
    const subject = `Discard ${uid()}`
    const share = await createShare(await tokenOf(sender), { users: [to.id], subject, files: { 'd.txt': 'd' } })
    await signIn(page, sender)
    await page.goto(`/share/${share.id}`)
    await page.getByRole('button', { name: 'Discard share' }).click()
    await confirmDialog(page).click()
    await expect(page.getByText('Share discarded.')).toBeVisible()
  })
})
