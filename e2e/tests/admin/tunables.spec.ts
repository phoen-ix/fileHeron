import { api, expect, test, toast } from './_admin'

/* The registry tunables (components/admin/TunableFields.vue): every page that
 * renders some saves one, keeps it across a reload, and resets it to the
 * default. The keys are ones whose change cannot disturb a parallel journey,
 * and each test restores what it found. */

type Item = { key: string; value: number | boolean | string; default: number | boolean | string }

const CASES: Array<[route: string, path: string, key: string, next: number | boolean | string]> = [
  ['admin-settings-sessions', '/admin/settings/sessions', 'auth.session_max_age_days', 31],
  ['admin-settings-sign-in', '/admin/settings/sign-in', 'rate_limit.lockout_duration_min', 16],
  ['admin-settings-transfers', '/admin/settings/transfers', 'downloads.resume_credit_hours', 25],
  ['admin-settings-anomaly', '/admin/settings/anomaly', 'anomaly.multi_network_threshold', 5],
  ['admin-settings-anomaly (switch)', '/admin/settings/anomaly', 'anomaly.enabled', false],
  ['admin-settings-advanced', '/admin/settings/advanced', 'retention.invite_days', 15],
  ['admin-settings-public-links', '/admin/settings/public-links', 'public_link.lockout_sec', 901],
  ['admin-settings-branding', '/admin/settings/branding', 'branding.app_name', 'fileHeron E2E'],
  ['admin-settings-secrets', '/admin/settings/secrets', 'secrets.max_views', 101],
  [
    'admin-settings-error-alerts',
    '/admin/settings/error-alerts',
    'error_log.scan_capture_per_min',
    301,
  ],
  ['admin-system', '/admin/system', 'updates.backup_keep', 4],
]

async function current(key: string): Promise<Item> {
  const r = await api<{ items: Item[] }>('/api/admin/settings/advanced')
  return r.items.find((i) => i.key === key)!
}

async function restore(item: Item): Promise<void> {
  await api('/api/admin/settings/advanced', {
    method: 'PUT',
    body: JSON.stringify({
      updates: { [item.key]: item.value === item.default ? null : item.value },
    }),
  })
}

for (const [name, path, key, next] of CASES) {
  test(`${name}: ${key} is saved, kept and reset`, async ({ admin: page }) => {
    const before = await current(key)
    try {
      await page.goto(path)
      const labelText = (await page.locator(`label[for="tunable-${key}"]`).textContent())!.trim()
      const field = page.getByLabel(labelText, { exact: true })
      const row = page.locator('.field-row', { has: field })
      const save = row.locator('xpath=ancestor::form').getByRole('button', { name: 'Save' })
      await expect(save).toBeDisabled()

      if (typeof next === 'boolean') await field.setChecked(next)
      else await field.fill(String(next))
      await save.click()
      await expect(toast(page, 'Advanced settings saved.')).toBeVisible()
      expect((await current(key)).value).toBe(next)

      await page.reload()
      if (typeof next === 'boolean') await expect(field).toBeChecked({ checked: next })
      else await expect(field).toHaveValue(String(next))

      await row.getByRole('button', { name: /^Reset .* to its default/ }).click()
      await save.click()
      await expect(toast(page, 'Advanced settings saved.')).toBeVisible()
      expect((await current(key)).value).toBe(before.default)
    } finally {
      await restore(before)
    }
  })
}

test('a value outside its bounds is stopped in the form and nothing is saved', async ({
  admin: page,
}) => {
  const before = await current('retention.invite_days')
  await page.goto('/admin/settings/advanced')
  const labelText = (await page
    .locator('label[for="tunable-retention.invite_days"]')
    .textContent())!.trim()
  const field = page.getByLabel(labelText, { exact: true })
  await field.fill('999999')
  await page.getByRole('button', { name: 'Save' }).click()
  // The browser's own constraint validation stops the submit (min/max).
  expect(await field.evaluate((el: HTMLInputElement) => el.validity.rangeOverflow)).toBe(true)
  await expect(toast(page, 'Advanced settings saved.')).toHaveCount(0)
  expect((await current('retention.invite_days')).value).toBe(before.value)
})
