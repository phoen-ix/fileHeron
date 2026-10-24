import { readFileSync } from 'node:fs'

import type { Page } from '@playwright/test'

import { BASE } from '../../helpers'
import { api, expect, newUser, py, test, toast, uid, uploadFile } from './_admin'

/* Sharing › Quarantine: an infected upload is listed, downloaded for review,
 * released to its owner with a reason, or purged. The stack runs without
 * ClamAV (AV_SKIP), so a file is quarantined with the worker's own call,
 * `quarantine_file`, exactly as an infected verdict would. */

async function infectedUpload(
  label: string,
): Promise<{ name: string; fileId: string; shareId: string }> {
  const u = await newUser('employee', label)
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email: u.email, password: u.password }),
  })
  const token = (await r.json()).access_token as string
  const name = `${label}-${uid()}.txt`
  const { fileId, shareId } = await uploadFile(token, name, 'X5O!P%@AP-not-really-eicar')
  // The upload is released clean first (AV_SKIP), then flagged.
  await expect
    .poll(async () => JSON.stringify(await api(`/api/shares/${shareId}`)), { timeout: 30_000 })
    .toContain('"clean"')
  py(
    [
      'import sys',
      'from app.database import SessionLocal',
      'from app.models.file import File',
      'from app.services.quarantine import quarantine_file',
      'db = SessionLocal()',
      'quarantine_file(db, file=db.get(File, sys.argv[1]), signature="Eicar-Test-Signature")',
      'db.commit()',
    ].join('\n'),
    fileId,
  )
  return { name, fileId, shareId }
}

async function decide(page: Page, name: string, action: 'Release' | 'Purge', reason: string) {
  const row = page.locator('tbody tr', { hasText: name })
  await row.getByRole('button', { name: action, exact: true }).click()
  const box = page.locator('.confirm-backdrop')
  const go = box.getByRole('button', { name: action, exact: true })
  if (action === 'Release') {
    // A release needs a reason for the audit log; a purge does not.
    await box.locator('textarea').fill('too short')
    await expect(go).toBeDisabled()
    await box.locator('textarea').fill(reason)
  }
  await go.click()
}

test('an infected file is listed, downloaded for review and released', async ({ admin: page }) => {
  const f = await infectedUpload('release')
  await page.goto('/admin/quarantine')
  await page.getByRole('searchbox').fill(f.name)
  const row = page.locator('tbody tr', { hasText: f.name })
  await expect(row).toHaveCount(1)

  const dl = page.waitForEvent('download')
  await row.getByRole('button', { name: 'Download' }).click()
  expect(readFileSync(await (await dl).path(), 'utf8')).toContain('not-really-eicar')

  // Cancel leaves it alone.
  await row.getByRole('button', { name: 'Release', exact: true }).click()
  await page.locator('.confirm-backdrop').getByRole('button', { name: 'Cancel' }).click()
  await expect(row).toHaveCount(1)

  await decide(page, f.name, 'Release', 'False positive confirmed by the e2e suite.')
  await expect(toast(page, 'File released back to the owner.')).toBeVisible()
  await expect(row).toHaveCount(0)
  const share = await api<{ files: Array<{ id: string; state: string }> }>(
    `/api/shares/${f.shareId}`,
  )
  expect(share.files.find((x) => x.id === f.fileId)?.state).toBe('clean')
})

test('an infected file is purged; file history keeps the record', async ({ admin: page }) => {
  const f = await infectedUpload('purge')
  await page.goto('/admin/quarantine')
  await decide(page, f.name, 'Purge', 'Confirmed malware, removing the bytes.')
  await expect(toast(page, 'File bytes purged from disk.')).toBeVisible()
  await expect(page.locator('tbody tr', { hasText: f.name })).toHaveCount(0)

  await page.goto('/admin/file-history')
  await page.getByRole('checkbox', { name: 'Show deleted / abandoned' }).check()
  await page.getByRole('searchbox').fill(f.name)
  await expect(page.locator('tbody tr', { hasText: f.name })).toBeVisible()
})

test('the uploader page links a quarantined file to the quarantine', async ({ admin: page }) => {
  const f = await infectedUpload('linked')
  const owner = await api<{ items: Array<{ uploader: { user_id?: number; id?: number } }> }>(
    `/api/admin/files?q=${encodeURIComponent(f.name)}&include_inactive=true`,
  )
  const uploaderId = owner.items[0].uploader.user_id ?? owner.items[0].uploader.id
  await page.goto(`/admin/users/${uploaderId}`)
  await page
    .locator('.file-item', { hasText: f.name })
    .getByRole('link', { name: 'In quarantine →' })
    .click()
  await expect(page).toHaveURL(/\/admin\/quarantine$/)
})
