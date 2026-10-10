import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { ADMIN, USER, apiFetch, apiLogin } from '../helpers'

/* Journey: folder uploads (v2.26.0). A picked folder keeps its structure: each
 * file lands with its folder, the share page groups the files under folder
 * headings, and the bulk ZIP carries the tree. The e2e frontend forces the
 * resumable path (VITE_DIRECT_UPLOAD_THRESHOLD=1), so this drives Uppy/tus with
 * a folder - the path a real project tree takes. */

test('a picked folder keeps its structure on the share page and in the ZIP', async ({ page }) => {
  const admin = await apiLogin(ADMIN.email, ADMIN.password)
  const search = await apiFetch(admin, `/api/users/search?q=${encodeURIComponent(USER.email)}`)
  const recipientId = ((await search.json()).items as Array<{ user_id: number; email: string }>)
    .find((u) => u.email === USER.email)?.user_id
  expect(recipientId).toBeTruthy()
  const sh = await apiFetch(admin, '/api/shares', {
    method: 'POST',
    body: JSON.stringify({
      kind: 'outbound',
      recipients: { user_ids: [recipientId], group_ids: [] },
      expires_at: null,
      subject: `E2E folder ${Date.now()}`,
    }),
  })
  expect(sh.status).toBe(201)
  const shareId = (await sh.json()).id as string

  // A small tree on disk; the picked folder's own name becomes the top folder.
  const project = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'fh-folder-')), 'Project')
  fs.mkdirSync(path.join(project, 'docs', '2026'), { recursive: true })
  fs.writeFileSync(path.join(project, 'readme.txt'), 'top')
  fs.writeFileSync(path.join(project, 'docs', 'a.txt'), 'aaa')
  fs.writeFileSync(path.join(project, 'docs', '2026', 'b.txt'), 'bbbb')

  await page.goto('/login')
  await page.fill('#login-email', ADMIN.email)
  await page.fill('#login-password', ADMIN.password)
  await page.click('button[type=submit]')
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
  await page.goto(`/share/${shareId}`)

  await page.getByTestId('add-files-toggle').click()
  await page.locator('.add-files-panel input.folder-input').setInputFiles(project)
  await page.getByTestId('add-files-submit').click()
  await expect(page.getByTestId('add-files-toggle')).toBeVisible({ timeout: 30_000 })

  // Each file landed with its folder - read from the API, not the UI.
  type Row = { original_filename: string; relative_dir: string | null; state: string }
  let rows: Row[] = []
  await expect
    .poll(
      async () => {
        rows = (await (await apiFetch(admin, `/api/shares/${shareId}`)).json()).files as Row[]
        return rows.length > 0 && rows.every((f) => f.state === 'clean')
      },
      { timeout: 30_000 },
    )
    .toBe(true)
  expect(rows.map((f) => [f.relative_dir, f.original_filename])).toEqual([
    ['Project', 'readme.txt'],
    ['Project/docs', 'a.txt'],
    ['Project/docs/2026', 'b.txt'],
  ])

  // The page groups them under one heading per folder.
  await page.reload()
  await expect(page.locator('.folder-heading')).toHaveText([
    'Project/',
    'Project/docs/',
    'Project/docs/2026/',
  ])

  // The bulk ZIP carries the tree (members are STORED, so names are plain bytes).
  const minted = await apiFetch(admin, `/api/files/${shareId}/download-zip-url`)
  expect(minted.status).toBe(200)
  const url = (await minted.json()).url as string
  const zip = url.startsWith('http') ? await fetch(url) : await apiFetch(admin, url)
  expect(zip.status).toBe(200)
  const bytes = Buffer.from(await zip.arrayBuffer())
  for (const member of ['Project/readme.txt', 'Project/docs/a.txt', 'Project/docs/2026/b.txt']) {
    expect(bytes.includes(Buffer.from(member)), member).toBe(true)
  }
})
