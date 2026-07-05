import { expect, test } from '@playwright/test'

import { ADMIN, USER, apiFetch, apiLogin } from '../helpers'

/* Journey: the resumable browser upload path (@uppy/core + @uppy/tus). The other
 * upload coverage uploads via a raw fetch to /api/uploads/direct, which never
 * runs the Uppy/TUS client. Here the e2e frontend is built with
 * VITE_DIRECT_UPLOAD_THRESHOLD=1 (docker-compose.e2e.yml), so ANY file the
 * browser adds takes the resumable path - a tiny file exercises the real Uppy
 * client end-to-end against tusd. A broken Uppy upgrade surfaces here: the
 * upload errors, the file never registers, and the assertions below fail. */

test('resumable browser upload (Uppy/TUS) adds a file to an active share', async ({
  page,
}) => {
  const admin = await apiLogin(ADMIN.email, ADMIN.password)

  // Recipient id + an active directed share, created via the API so this test
  // drives ONLY the browser upload UI.
  const search = await apiFetch(admin, `/api/users/search?q=${encodeURIComponent(USER.email)}`)
  const recipientId = ((await search.json()).items as Array<{ user_id: number; email: string }>)
    .find((u) => u.email === USER.email)?.user_id
  expect(recipientId).toBeTruthy()

  const subject = `E2E resumable ${Date.now()}`
  const sh = await apiFetch(admin, '/api/shares', {
    method: 'POST',
    body: JSON.stringify({
      kind: 'outbound',
      recipients: { user_ids: [recipientId], group_ids: [] },
      expires_at: null,
      subject,
    }),
  })
  expect(sh.status).toBe(201)
  const shareId = (await sh.json()).id as string

  // Log in as the owner (admin) in the browser and open the share.
  await page.goto('/login')
  await page.fill('#login-email', ADMIN.email)
  await page.fill('#login-password', ADMIN.password)
  await page.click('button[type=submit]')
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
  await page.goto(`/share/${shareId}`)

  // Open the owner add-files panel and hand the hidden <input type=file> a tiny
  // file; with the forced threshold this goes through the Uppy/TUS client.
  await page.getByTestId('add-files-toggle').click()
  const fileName = `resumable-${Date.now()}.bin`
  await page.locator('.add-files-panel input[type=file]').setInputFiles({
    name: fileName,
    mimeType: 'application/octet-stream',
    buffer: Buffer.from([1, 2, 3, 4, 5, 6, 7, 8]),
  })
  await page.getByTestId('add-files-submit').click()

  // On success the panel closes (the toggle reappears) and the file is now on
  // the share. Assert both - the API check proves the bytes actually landed via
  // tusd, not just a UI state flip.
  await expect(page.getByTestId('add-files-toggle')).toBeVisible({ timeout: 30_000 })

  const detail = await apiFetch(admin, `/api/shares/${shareId}`)
  const files = (await detail.json()).files as Array<{ id: string; original_filename: string }>
  expect(files.some((f) => f.original_filename === fileName)).toBeTruthy()
})
