import { api, confirm, expect, test, toast } from './_admin'

/* Security › Blocked sources: block an address or a range by hand, filter
 * the list, release, release + allow, release all, and the allowlist. The
 * addresses are globally routable ones that never reach the e2e stack (only
 * global addresses can be blocked); every block is released again. */

const octet = () => 1 + Math.floor(Math.random() * 250)
const globalIp = () => `45.${octet()}.${octet()}.${octet()}`

async function releaseAll() {
  await api('/api/admin/scan-guard/blocks/release-all', { method: 'POST' })
}

async function block(page: import('@playwright/test').Page, subject: string, note: string) {
  await page.getByRole('textbox', { name: 'Address or range' }).fill(subject)
  await page.getByRole('combobox', { name: 'Duration' }).selectOption('60')
  await page.getByRole('textbox', { name: 'Note (optional)' }).fill(note)
  await page.getByRole('button', { name: 'Block', exact: true }).click()
}

test.afterAll(releaseAll)

test('block an address by hand, find it, release it', async ({ admin: page }) => {
  const ip = globalIp()
  await page.goto('/admin/ip-blocks')
  await block(page, ip, 'e2e: manual block')
  await expect(page.getByRole('dialog')).toContainText(`Block ${ip} for 1 hour?`)
  await confirm(page)
  await expect(toast(page, `${ip} is blocked.`)).toBeVisible()

  await page.getByRole('searchbox').fill(ip)
  const row = page.locator('tbody tr', { hasText: ip })
  await expect(row).toContainText('Blocked by an admin')
  await page.getByRole('combobox', { name: 'Origin' }).selectOption('auto')
  await expect(row).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Origin' }).selectOption('manual')
  await expect(row).toHaveCount(1)

  await row.getByRole('button', { name: 'Release', exact: true }).click()
  await confirm(page)
  await expect(toast(page, `${ip} released.`)).toBeVisible()
  await page.getByRole('combobox', { name: 'Status' }).selectOption('released')
  await expect(row).toContainText('Released')
})

test('block a range, release + allow it, then remove it from the allowlist', async ({
  admin: page,
}) => {
  const range = `45.${octet()}.${octet()}.0/24`
  await page.goto('/admin/ip-blocks')
  await block(page, range, 'e2e: range')
  await expect(page.getByRole('dialog')).toContainText('256 addresses')
  await confirm(page)
  await expect(toast(page, `${range} is blocked.`)).toBeVisible()

  await page.getByRole('checkbox', { name: 'Ranges only' }).check()
  const row = page.locator('tbody tr', { hasText: range })
  await expect(row).toContainText('range')
  await row.getByRole('button', { name: 'Release + allow' }).click()
  await confirm(page)
  await expect(toast(page, `${range} released and allowlisted.`)).toBeVisible()

  const entry = page.locator('.allowlist li, .allow-list li, ul li', { hasText: range }).last()
  await expect(entry).toBeVisible()
  await entry.getByRole('button', { name: 'Remove' }).click()
  await confirm(page)
  await expect(toast(page, `${range} removed from the allowlist.`)).toBeVisible()
})

test('the allowlist takes an entry directly; a non-global address cannot be blocked', async ({
  admin: page,
}) => {
  const ip = globalIp()
  await page.goto('/admin/ip-blocks')
  await page.getByRole('textbox', { name: 'Never block these' }).fill(ip)
  await page.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(toast(page, `${ip} will never be blocked.`)).toBeVisible()
  const allow = await api<{ entries: string[] }>('/api/admin/scan-guard/allowlist')
  expect(JSON.stringify(allow)).toContain(ip)
  await api('/api/admin/scan-guard/allowlist', {
    method: 'DELETE',
    body: JSON.stringify({ entry: ip }),
  }).catch(() => undefined)

  await block(page, '10.1.2.3', 'private')
  await confirm(page).catch(() => undefined)
  await expect(
    page.locator('.toast[data-tone="error"], .block-form [role="alert"]').first(),
  ).toBeVisible()
})

test('release all lifts every block in force', async ({ admin: page }) => {
  const a = globalIp()
  const b = globalIp()
  await page.goto('/admin/ip-blocks')
  for (const ip of [a, b]) {
    await block(page, ip, 'e2e: release all')
    await confirm(page)
    await expect(toast(page, `${ip} is blocked.`)).toBeVisible()
  }
  await page.getByRole('button', { name: 'Release all' }).click()
  await confirm(page)
  await expect(toast(page, /blocks? released\./)).toBeVisible()
  await expect(page.getByText('Nothing is being refused right now.')).toBeVisible()
  await page.getByRole('button', { name: 'Show all' }).click()
  await expect(page.getByRole('combobox', { name: 'Status' })).toHaveValue('all')
})

test('the watchlist explains why it is empty', async ({ admin: page }) => {
  await page.goto('/admin/ip-blocks')
  await expect(
    page
      .getByText('The watchlist is turned off in the scan-guard settings.')
      .or(page.getByText('Nothing is being watched right now.')),
  ).toBeVisible()
})
