import { expect, test } from '@playwright/test'

import { ADMIN, BASE, USER, apiFetch, apiLogin } from '../helpers'

/* Journey 3: a client submits an inbound share + file to the company; staff see
 * it in their inbox (UI) and can download the file (recipient-bearer path).
 * Inbound (no explicit recipient) avoids depending on the recipient-search shape. */

test('inbound share + upload is visible in staff inbox and downloadable', async ({ page }) => {
  const userTok = await apiLogin(USER.email, USER.password)
  const subject = `E2E inbound ${Date.now()}`

  const sh = await apiFetch(userTok, '/api/shares', {
    method: 'POST',
    body: JSON.stringify({
      kind: 'inbound',
      recipients: { user_ids: [], group_ids: [] },
      expires_at: null,
      subject,
    }),
  })
  expect(sh.status).toBe(201)
  const shareId = (await sh.json()).id as string

  const form = new FormData()
  form.append('share_id', shareId)
  form.append(
    'file',
    new Blob([new Uint8Array([1, 2, 3, 4, 5])], { type: 'application/octet-stream' }),
    'hello.bin',
  )
  const up = await fetch(`${BASE}/api/uploads/direct`, {
    method: 'POST',
    headers: { authorization: `Bearer ${userTok}` },
    body: form,
  })
  if (!up.ok) throw new Error(`direct upload failed: ${up.status} ${await up.text()}`)

  // Staff (admin) sees the inbound share in their inbox.
  await page.goto('/login')
  await page.fill('#login-email', ADMIN.email)
  await page.fill('#login-password', ADMIN.password)
  await page.click('button[type=submit]')
  await page.goto('/inbox')
  await expect(page.getByText(subject)).toBeVisible()

  // ...and can download the file via the authenticated download path.
  const adminTok = await apiLogin(ADMIN.email, ADMIN.password)
  const detail = await apiFetch(adminTok, `/api/shares/${shareId}`)
  expect(detail.ok).toBeTruthy()
  const fileId = (await detail.json()).files[0].id as string
  const dl = await apiFetch(adminTok, `/api/files/${fileId}/download`)
  expect(dl.status).toBe(200)
})
