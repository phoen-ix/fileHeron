import type { Browser, Locator, Page } from '@playwright/test'

import { apiFetch } from '../../helpers'
import {
  connect,
  createShare,
  expect,
  mailedToken,
  newUser,
  ok,
  restoreSettings,
  session,
  setSettings,
  signIn,
  test,
  tokenOf,
  uid,
} from './lib'

/* Public links from the owner's share page to an anonymous visitor: create
 * one with a password and a download limit, unlock and download it, the ZIP,
 * revoking it, an exhausted and an expired link, an unknown token - and a link
 * attached while composing a share, mailed to an address with no account. */

const confirmDialog = (page: Page) => page.getByRole('dialog').getByRole('button').last()

async function download(page: Page, trigger: Locator): Promise<{ name: string; text: string }> {
  const [dl] = await Promise.all([page.waitForEvent('download'), trigger.click()])
  const chunks: Buffer[] = []
  for await (const c of await dl.createReadStream()) chunks.push(c as Buffer)
  return { name: dl.suggestedFilename(), text: Buffer.concat(chunks).toString('latin1') }
}

test('the owner creates a protected link; a visitor unlocks, downloads, and loses it on revoke', async ({
  page,
  browser,
}) => {
  const owner = await newUser('employee', 'linkowner')
  const to = await newUser('client', 'linkto')
  await connect(owner, to)
  const subject = `Linked ${uid()}`
  const share = await createShare(await tokenOf(owner), {
    users: [to.id],
    subject,
    files: { 'plan.txt': 'the plan', 'notes.txt': 'notes' },
  })
  await signIn(page, owner)
  await page.goto(`/share/${share.id}`)
  const panel = page.locator('.public-link-panel')
  await panel.getByLabel('Password (optional)').fill('open-sesame-42')
  await panel.getByLabel('Download limit (optional)').fill('3')
  await panel.getByLabel('Email me on each download').check()
  await panel.getByRole('button', { name: 'Create public link' }).click()
  await expect(page.getByText('Public link created.')).toBeVisible()
  await expect(panel.locator('.qr-svg svg')).toBeVisible()
  const url = (await panel.locator('pre.url').innerText()).trim()
  expect(url).toMatch(/\/d\/[A-Za-z0-9_-]+$/)
  const path = new URL(url).pathname

  const visitor = await session(browser)
  await visitor.goto(path)
  // Locked, it shows neither the subject nor the files.
  await expect(visitor.getByRole('heading', { level: 1, name: 'Files for you' })).toBeVisible()
  await expect(visitor.getByText('plan.txt')).toHaveCount(0)
  await visitor.getByLabel('Password').fill('wrong-password')
  await visitor.getByRole('button', { name: 'Unlock' }).click()
  await expect(visitor.getByRole('alert')).toBeVisible()
  await visitor.getByLabel('Password').fill('open-sesame-42')
  await visitor.getByRole('button', { name: 'Unlock' }).click()
  await expect(visitor.getByRole('heading', { name: 'Files (2)' })).toBeVisible()
  await expect(visitor.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  await expect(visitor.getByText('3 downloads left')).toBeVisible()

  const plan = visitor.locator('.file-row', { hasText: 'plan.txt' })
  expect(await download(visitor, plan.getByRole('link', { name: 'Download' }))).toEqual({
    name: 'plan.txt',
    text: 'the plan',
  })
  await expect(visitor.getByText('2 downloads left')).toBeVisible()
  await visitor.locator('.file-row', { hasText: 'notes.txt' }).getByRole('button', { name: 'Preview' }).click()
  await expect(visitor.getByRole('dialog')).toContainText('notes')
  await visitor.keyboard.press('Escape')
  await expect(visitor.getByRole('dialog')).toHaveCount(0)
  const zip = await download(visitor, visitor.getByRole('link', { name: /Download all as ZIP/ }))
  expect(zip.text).toContain('notes.txt')

  // The owner was told, and sees the count; then revokes.
  await page.reload()
  await page.getByRole('button', { name: 'Notifications' }).click()
  await expect(page.getByText('Public link downloaded: plan.txt')).toBeVisible()
  await page.getByRole('button', { name: 'Notifications' }).click()
  await expect(panel.getByText('2 of 3 used')).toBeVisible()
  await panel.getByRole('button', { name: 'Revoke link' }).click()
  await confirmDialog(page).click()
  await expect(page.getByText('Public link revoked.')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Create public link' })).toBeVisible()

  await visitor.reload()
  await expect(visitor.getByRole('heading', { level: 1, name: 'This link has been revoked.' })).toBeVisible()
  await visitor.context().close()
})

test('an exhausted link, an ended share and an unknown token each say so', async ({ page }) => {
  const token = await tokenOf(await newUser('employee', 'linkstates'))
  const exhausted = await createShare(token, {
    publicLink: { password: null, download_limit: 1 },
    files: { 'once.txt': 'once' },
  })
  const ended = await createShare(token, { publicLink: { password: null }, files: { 'e.txt': 'e' } })
  await ok(apiFetch(token, `/api/shares/${ended.id}/expire`, { method: 'POST' }), 'expire')

  const once = new URL(exhausted.linkUrl!).pathname
  await page.goto(once)
  await download(page, page.locator('.file-row').getByRole('link', { name: 'Download' }))
  await page.reload()
  await expect(
    page.getByRole('heading', { level: 1, name: 'This link has reached its download limit.' }),
  ).toBeVisible()

  await page.goto(new URL(ended.linkUrl!).pathname)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(
    /This share has expired\.|This share is no longer active\.|This link has been revoked\./,
  )

  await page.goto('/d/this-token-does-not-exist')
  await expect(page.getByRole('heading', { level: 1, name: 'This link is invalid.' })).toBeVisible()
})

test('a link attached while composing is mailed to an address with no account', async ({ page, browser }) => {
  const policy = '/api/admin/settings/public-links/policy'
  const before = await setSettings(policy, { external_recipients_enabled: true })
  try {
    await composeToOutsider(page, browser)
  } finally {
    await restoreSettings(policy, before)
  }
})

async function composeToOutsider(page: Page, browser: Browser): Promise<void> {
  const sender = await newUser('employee', 'external')
  const outsider = `outsider-${uid()}@e2e.example`
  const subject = `External ${uid()}`
  await signIn(page, sender)
  await page.goto('/share/new')
  await page
    .locator('input.dropzone-input')
    .setInputFiles({ name: 'brief.txt', mimeType: 'text/plain', buffer: Buffer.from('brief') })
  await page.getByLabel('Subject', { exact: true }).fill(subject)
  const box = page.locator('.recipient-picker').getByRole('combobox')
  await box.fill(outsider)
  const opt = page.getByTestId('external-option')
  await expect(async () => {
    if (!(await opt.isVisible())) await box.press('ArrowDown')
    await expect(opt).toBeVisible({ timeout: 1_000 })
  }).toPass()
  await opt.click()
  await expect(page.getByText('Required: recipients without an account receive this link by email.')).toBeVisible()
  await expect(page.getByLabel('Email the download link to these addresses')).toBeChecked()
  await page.getByRole('button', { name: 'Create + send' }).click()
  await expect(page.getByText('All files sent')).toBeVisible({ timeout: 30_000 })
  const shown = (await page.locator('.plaintext-value, pre, code').filter({ hasText: '/d/' }).first().innerText()).trim()
  await page.getByRole('button', { name: 'View share' }).click()
  await expect(page.getByTestId('external-recipients')).toContainText(outsider)
  await expect(page.getByTestId('external-recipients')).toContainText('link emailed')

  const token = await mailedToken('d', outsider)
  expect(shown).toContain(`/d/${token}`)
  const visitor = await session(browser)
  await visitor.goto(`/d/${token}`)
  await expect(visitor.getByRole('heading', { level: 1, name: subject })).toBeVisible()
  await expect(visitor.getByText('brief.txt')).toBeVisible()
  await visitor.context().close()
}
