import type { Page } from '@playwright/test'

import { api, confirm, expect, test, toast, uid } from './_admin'

/* Email › Webhooks: create (the signing secret is shown once), test, see the
 * deliveries, a real event, retry a failed delivery, disable/enable, delete,
 * and the address policy. The receivers live inside the compose network:
 * private addresses are allowed for webhooks (never loopback), and the
 * backend's own telemetry beacon answers any POST with 204, while nginx
 * answers a POST to the SPA with an error status. */

const OK_SINK = 'http://backend:8000/api/telemetry/page-404'
const FAILING_SINK = 'http://frontend/no-such-hook'

async function create(page: Page, name: string, url: string, events: string[]) {
  await page.getByRole('textbox', { name: 'Name' }).fill(name)
  await page.getByRole('textbox', { name: 'Endpoint URL' }).fill(url)
  for (const e of events) await page.getByRole('checkbox', { name: e }).check()
  await page.getByRole('button', { name: 'Create webhook' }).click()
}

function hook(page: Page, name: string) {
  return page.locator('li.hook', { hasText: name })
}

/** Opens (or re-opens) the deliveries panel until it shows `text`. */
async function expectDelivery(page: Page, name: string, text: RegExp, cell = 'td') {
  await expect(async () => {
    const h = hook(page, name)
    const panel = h.locator('.deliveries')
    if (await panel.count()) await h.getByRole('button', { name: 'Deliveries' }).click()
    await h.getByRole('button', { name: 'Deliveries' }).click()
    await expect(h.locator('.deliveries tbody tr').first().locator(cell)).toHaveText(text, {
      timeout: 1000,
    })
  }).toPass({ timeout: 30_000 })
}

test('create a webhook, test it, and see the delivery succeed', async ({ admin: page }) => {
  const name = `Ops hook ${uid()}`
  await page.goto('/admin/settings/webhooks')
  await page.getByRole('button', { name: 'Create webhook' }).click()
  await expect(toast(page, 'Enter a name, a URL, and at least one event.')).toBeVisible()

  await page.getByRole('button', { name: 'Select all' }).click()
  await expect(page.getByRole('checkbox', { name: 'Secret used up' })).toBeChecked()
  await page.getByRole('button', { name: 'Deselect all' }).click()
  await expect(page.getByRole('checkbox', { name: 'Secret used up' })).not.toBeChecked()

  await create(page, name, OK_SINK, ['Share created'])
  await expect(page.getByText('Signing secret - shown once')).toBeVisible()
  const secret = (await page.locator('pre.secret').textContent())!.trim()
  expect(secret.length).toBeGreaterThan(20)
  await page.locator('.created-box').getByRole('button', { name: 'Copy' }).click()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(secret)
  await page.getByRole('button', { name: "I've saved it" }).click()
  await expect(page.locator('pre.secret')).toHaveCount(0)

  const h = hook(page, name)
  await expect(h).toContainText('Active')
  await expect(h.locator('.event-tag')).toHaveText(['Share created'])
  await h.getByRole('button', { name: 'Test' }).click()
  await expect(toast(page, 'Test event queued.')).toBeVisible()
  await expectDelivery(page, name, /^204$/, 'td.num >> nth=0')

  // A real event is delivered as well.
  await api('/api/shares', {
    method: 'POST',
    body: JSON.stringify({
      kind: 'outbound',
      recipients: { user_ids: [], group_ids: [] },
      expires_at: null,
      subject: `Hooked ${uid()}`,
      public_link: { password: null, expires_at: null, max_downloads: null },
    }),
  })
  await expectDelivery(page, name, /^Share created$/, 'td >> nth=0')
})

test('a failing delivery is shown and can be retried; disable, enable, delete', async ({
  admin: page,
}) => {
  const name = `Broken hook ${uid()}`
  await page.goto('/admin/settings/webhooks')
  await create(page, name, FAILING_SINK, ['Share revoked'])
  await page.getByRole('button', { name: "I've saved it" }).click()
  const h = hook(page, name)
  await h.getByRole('button', { name: 'Test' }).click()
  await expectDelivery(page, name, /^[45]\d\d$/, 'td.num >> nth=0')
  await h.locator('.deliveries tbody tr').first().getByRole('button', { name: 'Retry' }).click()
  await expect(toast(page, 'Delivery re-queued.')).toBeVisible()

  await h.getByRole('button', { name: 'Disable' }).click()
  await expect(h).toContainText('Disabled')
  await h.getByRole('button', { name: 'Enable' }).click()
  await expect(h).toContainText('Active')

  await h.getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByRole('dialog')).toContainText(`Delete the webhook "${name}"?`)
  await confirm(page)
  await expect(h).toHaveCount(0)
})

test('a loopback address is refused as a webhook target', async ({ admin: page }) => {
  await page.goto('/admin/settings/webhooks')
  await create(page, `Loopback ${uid()}`, 'http://127.0.0.1:8000/x', ['Share created'])
  await expect(page.locator('.toast[data-tone="error"]').first()).toBeVisible()
  await expect(page.locator('pre.secret')).toHaveCount(0)
})
