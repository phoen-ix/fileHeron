import { expect, test } from '@playwright/test'

import { ADMIN, BASE, USER, apiFetch, apiLogin } from '../helpers'

/* Journey 3: admin sends a directed share + file to a user; the recipient sees
 * it in their inbox (UI) and can download it (recipient-bearer path). */

test('directed share + upload is visible in the recipient inbox and downloadable', async ({
  page,
}) => {
  const admin = await apiLogin(ADMIN.email, ADMIN.password)

  // Admin can address anyone - resolve the recipient's id.
  const search = await apiFetch(admin, `/api/users/search?q=${encodeURIComponent(USER.email)}`)
  const items = (await search.json()).items as Array<{ user_id: number; email: string }>
  const recipientId = items.find((u) => u.email === USER.email)?.user_id
  expect(recipientId).toBeTruthy()

  const subject = `E2E directed ${Date.now()}`
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

  const form = new FormData()
  form.append('share_id', shareId)
  form.append(
    'file',
    new Blob([new Uint8Array([1, 2, 3, 4, 5])], { type: 'application/octet-stream' }),
    'hello.bin',
  )
  const up = await fetch(`${BASE}/api/uploads/direct`, {
    method: 'POST',
    headers: { authorization: `Bearer ${admin}` },
    body: form,
  })
  if (!up.ok) throw new Error(`direct upload failed: ${up.status} ${await up.text()}`)

  // Recipient (USER) sees the share in their inbox.
  await page.goto('/login')
  await page.fill('#login-email', USER.email)
  await page.fill('#login-password', USER.password)
  await page.click('button[type=submit]')
  await page.goto('/inbox')
  await expect(page.getByText(subject)).toBeVisible()

  // ...and can download the file via the authenticated download path.
  const userTok = await apiLogin(USER.email, USER.password)
  const detail = await apiFetch(userTok, `/api/shares/${shareId}`)
  expect(detail.ok).toBeTruthy()
  const fileId = (await detail.json()).files[0].id as string
  const dl = await apiFetch(userTok, `/api/files/${fileId}/download`)
  expect(dl.status).toBe(200)
})
