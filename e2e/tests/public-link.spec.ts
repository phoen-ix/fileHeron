import { expect, test } from '@playwright/test'

import { ADMIN, BASE, apiFetch, apiLogin } from '../helpers'

/* Journey 4: a password-protected, download-limited public link. Anonymous
 * visitor unlocks via the UI; the download-limit exhaustion is checked via the
 * public API (the journey's two halves: the unlock UX + the budget enforcement). */

test('public link password unlock (UI) + download-limit exhaustion (API)', async ({ browser }) => {
  const admin = await apiLogin(ADMIN.email, ADMIN.password)
  const pw = 'link-pass-123'

  const sh = await apiFetch(admin, '/api/shares', {
    method: 'POST',
    body: JSON.stringify({
      kind: 'outbound',
      recipients: { user_ids: [], group_ids: [] },
      expires_at: null,
      subject: `E2E public ${Date.now()}`,
      public_link: { password: pw, download_limit: 1 },
    }),
  })
  expect(sh.status).toBe(201)
  const body = await sh.json()
  const shareId = body.id as string
  const token = (body.public_link.url as string).split('/d/')[1]

  const form = new FormData()
  form.append('share_id', shareId)
  form.append(
    'file',
    new Blob([new Uint8Array([9, 9, 9])], { type: 'application/octet-stream' }),
    'pub.bin',
  )
  const up = await fetch(`${BASE}/api/uploads/direct`, {
    method: 'POST',
    headers: { authorization: `Bearer ${admin}` },
    body: form,
  })
  expect(up.ok).toBeTruthy()

  // Anonymous unlock UX: wrong password errors, right password reveals the share.
  const anon = await browser.newContext()
  const pg = await anon.newPage()
  await pg.goto(`/d/${token}`)
  await pg.fill('.unlock-form input[type=password]', 'wrong-password')
  await pg.click('.unlock-form button')
  await expect(pg.locator('.unlock-form .fh-notice[data-tone=error]')).toBeVisible()
  await pg.fill('.unlock-form input[type=password]', pw)
  await pg.click('.unlock-form button')
  await expect(pg.locator('.unlock-form')).toHaveCount(0) // unlocked -> form gone
  await anon.close()

  // Download-limit exhaustion (limit=1): first anonymous download 200, second 410.
  const meta = await fetch(`${BASE}/api/public/${token}`)
  const fileId = (await meta.json()).files[0].id as string
  const unlock = await fetch(`${BASE}/api/public/${token}/unlock`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ password: pw }),
  })
  expect(unlock.ok).toBeTruthy()
  const cookie = (unlock.headers.get('set-cookie') ?? '').split(';')[0]
  const dl1 = await fetch(`${BASE}/api/public/${token}/files/${fileId}/download`, {
    headers: { cookie },
  })
  expect(dl1.status).toBe(200)
  const dl2 = await fetch(`${BASE}/api/public/${token}/files/${fileId}/download`, {
    headers: { cookie },
  })
  expect(dl2.status).toBe(410)
})
