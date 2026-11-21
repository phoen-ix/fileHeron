import type { Page } from '@playwright/test'

import { apiFetch } from '../../helpers'
import {
  connect,
  createShare,
  expect,
  group,
  newUser,
  ok,
  restoreSettings,
  setSettings,
  signIn,
  test,
  tokenOf,
  uid,
} from './lib'

/* The same thing done twice in a row, by mouse and typing alone
 * (COVERAGE.a.md, section R).
 *
 * Every other journey does each action once on a fresh page, which is how
 * "a second recipient cannot be added" shipped: after the first pick the
 * recipient list would not open again (fixed 2026-11-20). Here the SECOND use
 * is the point, and between the two nothing happens that a user would not do:
 * no extra key press, no blur, no wait, no click on a field that still has the
 * focus. A step like that added to make one of these pass is the defect. */

const confirmDialog = (page: Page) => page.getByRole('dialog').getByRole('button').last()
const file = (name: string, text: string) => ({ name, mimeType: 'text/plain', buffer: Buffer.from(text) })
const exactly = (text: string) => new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))

/** Click the recipient field ONCE; then, per entry, only type and click the
 * option. After each pick the field still has the focus, is empty, and typing
 * opens the list again. */
async function pickInARow(page: Page, queries: string[]): Promise<void> {
  const box = page.locator('.recipient-picker').getByRole('combobox')
  await box.click()
  for (const query of queries) {
    await page.keyboard.type(query)
    await page.getByRole('option', { name: exactly(query) }).click()
    await expect(box).toBeFocused()
    await expect(box).toHaveValue('')
  }
}

test('the share form takes two people, a group and an outside address in a row', async ({ page }) => {
  const policy = '/api/admin/settings/public-links/policy'
  const before = await setSettings(policy, { external_recipients_enabled: true })
  try {
    const sender = await newUser('employee', 'rowsender')
    const ann = await newUser('client', 'rowann')
    const bob = await newUser('client', 'rowbob')
    const member = await newUser('client', 'rowmember')
    const groupName = `Row group ${uid()}`
    await group(groupName, [member.id, sender.id])
    await connect(sender, ann, bob)
    const outsider = `row-${uid()}@e2e.example`
    const subject = `Four in a row ${uid()}`

    await signIn(page, sender)
    await page.goto('/share/new')
    await page.locator('input.dropzone-input').setInputFiles(file('a.txt', 'a'))
    await page.getByLabel('Subject', { exact: true }).fill(subject)
    await pickInARow(page, [ann.name, bob.name, groupName, outsider])
    await expect(page.locator('.recipient-picker .chip')).toHaveCount(4)
    await expect(page.getByTestId('submit-blockers')).toHaveCount(0)

    await page.getByRole('button', { name: 'Create + send' }).click()
    await expect(page.getByText('All files sent')).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: 'View share' }).click()
    await expect(page.getByRole('heading', { level: 1, name: subject })).toBeVisible()
    await expect(page.getByText(groupName)).toBeVisible()
    await expect(page.getByTestId('external-recipients')).toContainText(outsider)
    // The share page names groups and outside addresses, not the people: read
    // those from the share itself.
    const stored = await ok<{ recipient_user_ids: number[] }>(
      apiFetch(await tokenOf(sender), `/api/shares/${page.url().split('/').pop()}`),
      'read the share',
    )
    expect([...stored.recipient_user_ids].sort()).toEqual([ann.id, bob.id].sort())
  } finally {
    await restoreSettings(policy, before)
  }
})

test.describe('the secret forms', () => {
  let before: Record<string, unknown>
  test.beforeAll(async () => {
    before = await setSettings('/api/admin/settings/secrets', { enabled: true })
  })
  test.afterAll(async () => restoreSettings('/api/admin/settings/secrets', before))

  test('a secret and a secret request each take two people in a row', async ({ page }) => {
    const sender = await newUser('employee', 'rowsecret')
    const ann = await newUser('client', 'rowsann')
    const bob = await newUser('client', 'rowsbob')
    await connect(sender, ann, bob)
    await signIn(page, sender)

    await page.goto('/secrets')
    await page.getByRole('link', { name: 'New secret' }).first().click()
    await expect(page.getByRole('heading', { level: 1, name: 'Send a secret' })).toBeVisible()
    await page.getByTestId('secret-content-input').fill('two-in-a-row')
    await pickInARow(page, [ann.name, bob.name])
    await expect(page.locator('.recipient-picker .chip')).toHaveCount(2)
    await page.getByTestId('secret-submit').click()
    // Without a link the form goes straight to the secret, which lists both.
    await expect(page).toHaveURL(/\/secrets\/[^/]+$/)
    for (const person of [ann, bob]) await expect(page.getByText(person.name)).toBeVisible()

    await page.goto('/secrets')
    await page.getByTestId('request-secret').click()
    await expect(page.getByRole('heading', { level: 1, name: 'Request a secret' })).toBeVisible()
    await page.getByTestId('request-label').fill(`Asked twice ${uid()}`)
    await pickInARow(page, [ann.name, bob.name])
    await expect(page.locator('.recipient-picker .chip')).toHaveCount(2)
    await page.getByTestId('request-submit').click()
    await expect(page.getByRole('heading', { level: 1, name: /Asked twice/ })).toBeVisible()
    for (const person of [ann, bob]) await expect(page.getByText(person.name)).toBeVisible()
  })
})

test('"Create another" opens an empty form that sends a second share', async ({ page }) => {
  const sender = await newUser('employee', 'another')
  const to = await newUser('client', 'anotherto')
  await connect(sender, to)
  const first = `First ${uid()}`
  const second = `Second ${uid()}`
  await signIn(page, sender)
  await page.goto('/share/new')

  for (const subject of [first, second]) {
    await expect(page.getByLabel('Subject', { exact: true })).toHaveValue('')
    await expect(page.locator('.recipient-picker .chip')).toHaveCount(0)
    await page.locator('input.dropzone-input').setInputFiles(file(`${subject}.txt`, subject))
    await page.getByLabel('Subject', { exact: true }).fill(subject)
    await pickInARow(page, [to.name])
    await page.getByRole('button', { name: 'Create + send' }).click()
    await expect(page.getByText('All files sent')).toBeVisible({ timeout: 30_000 })
    if (subject === first) await page.getByRole('button', { name: 'Create another' }).click()
  }

  const sent = await ok<{ items: Array<{ subject: string | null }> }>(
    apiFetch(await tokenOf(sender), '/api/shares?box=outbox'),
    'list the outbox',
  )
  expect(sent.items.map((s) => s.subject).sort()).toEqual([first, second].sort())
})

test('the outbox filter follows a second person picked after the first', async ({ page }) => {
  const sender = await newUser('employee', 'filter')
  const ann = await newUser('client', 'filterann')
  const bob = await newUser('client', 'filterbob')
  await connect(sender, ann, bob)
  const token = await tokenOf(sender)
  const tag = uid()
  await createShare(token, { users: [ann.id], subject: `For Ann ${tag}` })
  await createShare(token, { users: [bob.id], subject: `For Bob ${tag}` })

  await signIn(page, sender)
  await page.goto('/outbox')
  await page.getByRole('combobox', { name: 'Any recipient' }).selectOption('user')
  const search = page.getByPlaceholder('Search by name or email…')
  for (const [person, subject] of [
    [bob, `For Bob ${tag}`],
    [ann, `For Ann ${tag}`],
  ] as const) {
    await search.fill(person.name)
    await page.getByRole('button', { name: exactly(person.name) }).click()
    await expect(page.locator('tbody tr')).toHaveCount(1)
    await expect(page.locator('tbody tr').first()).toContainText(subject)
    // The pick emptied the suggestions, and no late answer brings them back.
    await expect(page.locator('.suggestions')).toHaveCount(0)
  }
})

test('a public link is created, revoked and created again on the share page', async ({ page }) => {
  const owner = await newUser('employee', 'relink')
  const to = await newUser('client', 'relinkto')
  await connect(owner, to)
  const share = await createShare(await tokenOf(owner), { users: [to.id], files: { 'a.txt': 'a' } })
  await signIn(page, owner)
  await page.goto(`/share/${share.id}`)
  const panel = page.locator('.public-link-panel')

  const urls: string[] = []
  for (const round of [1, 2]) {
    // The form opens empty, the second time as the first.
    await expect(panel.getByLabel('Password (optional)')).toHaveValue('')
    await expect(panel.getByLabel('Download limit (optional)')).toHaveValue('')
    await panel.getByLabel('Download limit (optional)').fill(String(round + 1))
    await panel.getByRole('button', { name: 'Create public link' }).click()
    await expect(panel.locator('pre.url')).toBeVisible()
    urls.push((await panel.locator('pre.url').innerText()).trim())
    await panel.getByRole('button', { name: 'Revoke link' }).click()
    await confirmDialog(page).click()
    await expect(panel.getByRole('button', { name: 'Create public link' })).toBeVisible()
  }
  expect(urls[0]).toMatch(/\/d\/[A-Za-z0-9_-]+$/)
  expect(urls[1]).not.toBe(urls[0])
})

test('files are added to a share twice from its page', async ({ page }) => {
  const owner = await newUser('employee', 'addtwice')
  const to = await newUser('client', 'addtwiceto')
  await connect(owner, to)
  const token = await tokenOf(owner)
  const share = await createShare(token, { users: [to.id], files: { 'first.txt': 'one' } })
  await signIn(page, owner)
  await page.goto(`/share/${share.id}`)

  for (const name of ['second.txt', 'third.txt']) {
    await page.getByTestId('add-files-toggle').click()
    await page.locator('.add-files-panel input.dropzone-input').setInputFiles(file(name, name))
    await page.getByTestId('add-files-submit').click()
    await expect(page.getByTestId('add-files-toggle')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(name).first()).toBeVisible()
  }
  const now = await ok<{ files: Array<{ original_filename: string }> }>(
    apiFetch(token, `/api/shares/${share.id}`),
    'read the share',
  )
  expect(now.files.map((f) => f.original_filename).sort()).toEqual(['first.txt', 'second.txt', 'third.txt'])
})

test('two API tokens in a row; the password dialog opens clean the second time', async ({ page }) => {
  const p = await newUser('employee', 'twotokens')
  await signIn(page, p)
  await page.goto('/account')
  const panel = page.locator('#api-tokens')

  const tokens: string[] = []
  for (const name of ['first-script', 'second-script']) {
    await panel.getByRole('button', { name: 'Create token' }).click()
    await expect(panel.getByLabel('Token name')).toHaveValue('')
    await panel.getByLabel('Token name').fill(name)
    await panel.getByRole('checkbox', { name: 'List & read shares' }).check()
    await panel.getByRole('button', { name: 'Create', exact: true }).click()

    // Nothing left over from the last time it was open: no password, no error.
    await expect(page.getByTestId('step-up-password')).toHaveValue('')
    await expect(page.getByRole('dialog').getByRole('alert')).toHaveCount(0)
    if (name === 'first-script') {
      await page.getByTestId('step-up-password').fill('not-my-password-1')
      await page.getByTestId('step-up-confirm').click()
      await expect(page.getByRole('dialog').getByRole('alert')).toHaveText('Password incorrect.')
    }
    await page.getByTestId('step-up-password').fill(p.password)
    await page.getByTestId('step-up-confirm').click()

    tokens.push((await panel.locator('pre.plaintext-token').innerText()).trim())
    await panel.getByRole('button', { name: "I've saved it" }).click()
    await expect(panel.getByText(name)).toBeVisible()
  }
  expect(tokens[0]).toMatch(/^fh_[0-9a-f]{8}_/)
  expect(tokens[1]).not.toBe(tokens[0])
})
