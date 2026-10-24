import { BASE } from '../../helpers'
import { api, confirm, expect, newUser, test, uid, type Person } from './_admin'

/* Sharing › Secrets: the admin sees every secret and every secret request,
 * filters them, burns a secret (never reads it) and cancels a request. */

let sender: Person
let userId = 0

async function tokenOf(p: { email: string; password: string }) {
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email: p.email, password: p.password }),
  })
  return (await r.json()).access_token as string
}

test.beforeAll(async () => {
  await api('/api/admin/settings/secrets', {
    method: 'PUT',
    body: JSON.stringify({ enabled: true }),
  })
  sender = await newUser('employee', 'secret-sender')
  // An employee reaches another employee (clients only when connected).
  userId = (await newUser('employee', 'secret-recipient')).id
})

test.afterAll(async () => {
  await api('/api/admin/settings/secrets', {
    method: 'PUT',
    body: JSON.stringify({ enabled: false }),
  })
})

test('every secret is listed; the admin filters and burns one, without reading it', async ({
  admin: page,
}) => {
  const label = `VPN key ${uid()}`
  await api(
    '/api/secrets',
    {
      method: 'POST',
      body: JSON.stringify({
        content: 'the-secret-text-the-admin-must-never-see',
        label,
        max_views: 3,
        recipients: { user_ids: [userId], group_ids: [], emails: [] },
      }),
    },
    await tokenOf(sender),
  )
  await page.goto('/admin/secrets')
  await page.getByRole('searchbox').fill(label)
  const row = page.locator('tbody tr', { hasText: label })
  await expect(row).toContainText(sender.name)
  await expect(page.locator('body')).not.toContainText('the-secret-text-the-admin-must-never-see')

  await row.getByRole('button', { name: 'Burn' }).click()
  await expect(page.getByRole('dialog')).toContainText(`Burn this secret from ${sender.name}?`)
  await confirm(page)
  await page.getByRole('combobox').selectOption('active')
  await expect(row).toHaveCount(0)
  await page.getByRole('combobox').selectOption('revoked')
  await expect(row).toHaveCount(1)
})

test('every secret request is listed; the admin cancels one', async ({ admin: page }) => {
  const label = `Router password ${uid()}`
  await api(
    '/api/secret-requests',
    {
      method: 'POST',
      body: JSON.stringify({
        label,
        expires_at: new Date(Date.now() + 2 * 86_400_000).toISOString(),
        answer_max_views: 1,
        recipients: { user_ids: [userId], group_ids: [], emails: [] },
      }),
    },
    await tokenOf(sender),
  )
  await page.goto('/admin/secret-requests')
  await page.getByRole('searchbox').fill(label)
  const row = page.locator('tbody tr', { hasText: label })
  await expect(row).toContainText(sender.name)
  await row.getByRole('button', { name: /cancel/i }).click()
  await expect(page.getByRole('dialog')).toContainText(`Cancel ${sender.name}'s request?`)
  await confirm(page)
  // The list shows open requests by default; the cancelled one moves away.
  await expect(row).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Requests to show' }).selectOption('cancelled')
  await expect(row).toContainText('Cancelled')
})
