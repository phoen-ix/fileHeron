import type { Request } from '@playwright/test'

import { apiFetch, forceResumableUploads } from '../../helpers'
import { admin, connect, expect, newUser, ok, signIn, test, tokenOf } from './lib'

/* The resumable upload path (forced for these pages by
 * forceResumableUploads): a dropped connection mid-file resumes at the
 * server's offset instead of starting over, a file that keeps failing ends in
 * an error state with a working Retry, and a queued file can be removed
 * before sending. With a 1 KB ceiling the browser sends a small file directly
 * and a large one resumable. (Plain uploads, folders and the direct path
 * through the edge: upload-resumable, folder-upload and edge-behaviour specs.) */

const MB = 1024 * 1024
const CHUNK = 8 * MB // TUS_CHUNK_BYTES in frontend/src/composables/useUpload.ts

async function newShareForm(page: import('@playwright/test').Page, ceiling = 1) {
  const sender = await newUser('employee', 'uploader')
  const to = await newUser('client', 'uploadto')
  await connect(sender, to)
  await forceResumableUploads(page, ceiling)
  await signIn(page, sender)
  await page.goto('/share/new')
  const box = page.locator('.recipient-picker').getByRole('combobox')
  await box.fill(to.name)
  const opt = page.getByRole('option', { name: new RegExp(to.name) })
  await expect(async () => {
    if (!(await opt.isVisible())) await box.press('ArrowDown')
    await expect(opt).toBeVisible({ timeout: 1_000 })
  }).toPass()
  await opt.click()
  return { sender }
}

async function filesOf(token: string, shareUrl: string) {
  const id = shareUrl.split('/share/')[1]
  const share = await ok<{ files: Array<{ original_filename: string; size_bytes: number }> }>(
    apiFetch(token, `/api/shares/${id}`),
    'read share',
  )
  return share.files.map((f) => [f.original_filename, f.size_bytes])
}

test('under the direct-upload ceiling a file goes directly, at or over it resumable', async ({ page }) => {
  const { sender } = await newShareForm(page, 1024)
  const init: string[] = []
  let direct = 0
  let patches = 0
  page.on('request', (r) => {
    if (r.method() === 'PATCH' && r.url().includes('/uploads/')) patches++
    if (r.method() !== 'POST') return
    if (r.url().endsWith('/api/uploads/direct')) direct++
    if (r.url().endsWith('/api/uploads/init')) init.push(r.postDataJSON().filename)
  })
  await page.locator('input.dropzone-input').setInputFiles([
    { name: 'small.txt', mimeType: 'text/plain', buffer: Buffer.alloc(1023, 115) },
    { name: 'large.bin', mimeType: 'application/octet-stream', buffer: Buffer.alloc(1024, 108) },
  ])
  await page.getByRole('button', { name: 'Create + send' }).click()
  await expect(page.getByText('All files sent')).toBeVisible({ timeout: 30_000 })
  // Two files, one init (for the large one), so the small one went directly.
  expect(init).toEqual(['large.bin'])
  expect(direct).toBe(1)
  expect(patches).toBeGreaterThan(0)
  await page.getByRole('button', { name: 'View share' }).click()
  await expect(page).toHaveURL(/\/share\//)
  expect((await filesOf(await tokenOf(sender), page.url())).sort()).toEqual([
    ['large.bin', 1024],
    ['small.txt', 1023],
  ])
})

test('a connection dropped mid-file resumes at the server offset', async ({ page }) => {
  const { sender } = await newShareForm(page)
  const offsets: number[] = []
  let dropped = false
  await page.route('**/uploads/**', async (route, req: Request) => {
    if (req.method() !== 'PATCH') return route.fallback()
    const offset = Number(req.headers()['upload-offset'])
    offsets.push(offset)
    if (offset === CHUNK && !dropped) {
      dropped = true
      return route.abort('connectionreset')
    }
    return route.fallback()
  })
  const size = 12 * MB
  await page.locator('input.dropzone-input').setInputFiles({
    name: 'big.bin',
    mimeType: 'application/octet-stream',
    buffer: Buffer.alloc(size, 7),
  })
  await page.getByRole('button', { name: 'Create + send' }).click()
  await expect(page.getByText('All files sent')).toBeVisible({ timeout: 60_000 })
  // Chunk one, the dropped chunk two, chunk two again - never back to 0.
  expect(offsets).toEqual([0, CHUNK, CHUNK])
  await page.getByRole('button', { name: 'View share' }).click()
  await expect(page).toHaveURL(/\/share\//)
  expect(await filesOf(await tokenOf(sender), page.url())).toEqual([['big.bin', size]])
})

test('an upload that keeps failing shows its error, and Retry finishes it', async ({ page }) => {
  test.setTimeout(90_000)
  const { sender } = await newShareForm(page)
  const fail = (route: import('@playwright/test').Route, req: Request) =>
    req.method() === 'PATCH' ? route.abort('connectionreset') : route.fallback()
  await page.route('**/uploads/**', fail)
  await page.locator('input.dropzone-input').setInputFiles({
    name: 'flaky.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('flaky'),
  })
  await page.getByRole('button', { name: 'Create + send' }).click()
  // Uppy's own retries (0, 1, 3, 5, 10 s) run out first.
  await expect(page.getByText('1 file failed to upload. Retry below.')).toBeVisible({ timeout: 45_000 })
  await page.unroute('**/uploads/**', fail)
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByText('All files sent')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'View share' }).click()
  await expect(page).toHaveURL(/\/share\//)
  expect(await filesOf(await tokenOf(sender), page.url())).toEqual([['flaky.txt', 5]])
})

test('a queued file is removed before sending', async ({ page }) => {
  const { sender } = await newShareForm(page)
  await page.locator('input.dropzone-input').setInputFiles([
    { name: 'keep.txt', mimeType: 'text/plain', buffer: Buffer.from('keep') },
    { name: 'oops.txt', mimeType: 'text/plain', buffer: Buffer.from('oops') },
  ])
  const oops = page.locator('.upload-file-row, li', { hasText: 'oops.txt' })
  await oops.getByRole('button', { name: 'Remove' }).click()
  await expect(page.getByText('oops.txt')).toHaveCount(0)
  await page.getByRole('button', { name: 'Create + send' }).click()
  await expect(page.getByText('All files sent')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'View share' }).click()
  await expect(page).toHaveURL(/\/share\//)
  expect(await filesOf(await tokenOf(sender), page.url())).toEqual([['keep.txt', 4]])
})

test('an upload over the storage quota is refused with the reason', async ({ page }) => {
  const { sender } = await newShareForm(page)
  await ok(
    apiFetch(await admin(), `/api/admin/users/${sender.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ quota_bytes: 10 }),
    }),
    'set quota',
  )
  await page.locator('input.dropzone-input').setInputFiles({
    name: 'too-big.txt',
    mimeType: 'text/plain',
    buffer: Buffer.alloc(100, 120),
  })
  await page.getByRole('button', { name: 'Create + send' }).click()
  await expect(page.getByText('This upload would exceed your storage quota.', { exact: true })).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.getByText('1 file failed to upload. Retry below.')).toBeVisible()
})
