import { readFileSync } from 'node:fs'

import { api, expect, stepUp, test, toast } from './_admin'

/* System: Status & updates (version, release check, live checks, the update
 * source, automatic updates), Scheduled tasks, Backup & restore, and the
 * parts of Encryption at rest the encryption journey does not drive. The
 * release check never leaves the stack: the update source points at an
 * address inside it, so a check fails visibly instead of calling GitHub. */

const PASSWORD = 'AdminPass123!'
const UNREACHABLE_RELEASES = 'http://frontend/no-such-releases.json'

test.describe('status & updates', () => {
  test('version, refresh, live checks and the counters render', async ({ admin: page }) => {
    await page.goto('/admin/system')
    await expect(page.getByRole('heading', { name: 'Version' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Live checks' })).toBeVisible()
    await page.getByRole('button', { name: 'Refresh' }).click()
    await expect(page.getByText(/^Refreshed /)).toBeVisible()
    await page.getByRole('button', { name: 'Re-run' }).click()
    await expect(toast(page, 'Live checks refreshed.')).toBeVisible()
    const live = page.locator('section.card', {
      has: page.getByRole('heading', { name: 'Live checks' }),
    })
    await expect(live).toContainText('Database')
    await expect(live).toContainText('Redis')
    await expect(live).toContainText('Antivirus')
  })

  test('the update source is saved; a check against it reports the failure', async ({
    admin: page,
  }) => {
    const before = await api<{ api_url: string }>('/api/admin/settings/updates')
    try {
      await page.goto('/admin/system')
      const url = page.getByRole('textbox', { name: /^Release API URL/ })
      await url.fill(UNREACHABLE_RELEASES)
      await page.locator('form', { has: url }).getByRole('button', { name: 'Save' }).click()
      await expect(toast(page, 'Update settings saved.')).toBeVisible()
      await page.getByRole('button', { name: 'Check now' }).click()
      await expect(toast(page, /^Check failed: JSONDecodeError/)).toBeVisible({ timeout: 30_000 })
      // The version card shows the failed check without a reload.
      await expect(page.locator('dt', { hasText: /^Error$/ })).toBeVisible()
      await expect(page.locator('dd', { hasText: 'JSONDecodeError' })).toBeVisible()
    } finally {
      await api('/api/admin/settings/updates', {
        method: 'PUT',
        body: JSON.stringify({ api_url: before.api_url }),
      })
    }
  })

  test('automatic updates are switched on only with the password', async ({ admin: page }) => {
    const before = await api<{ enabled: boolean; scope: string; min_age_hours: number }>(
      '/api/admin/settings/auto-update',
    )
    try {
      await page.goto('/admin/system')
      await page.getByTestId('auto-update-enabled').check()
      await page.getByTestId('auto-update-scope').selectOption('patch')
      await page.getByTestId('auto-update-min-age').fill('48')
      await page.getByTestId('auto-update-save').click()
      await stepUp(page, 'not-the-password')
      await expect(page.getByTestId('step-up-dialog').getByRole('alert')).toBeVisible()
      await stepUp(page, PASSWORD)
      await expect(toast(page, 'Automatic update settings saved.')).toBeVisible()
      await expect(page.getByTestId('auto-update-line')).toBeVisible()
      await page.reload()
      await expect(page.getByTestId('auto-update-enabled')).toBeChecked()
      await expect(page.getByTestId('auto-update-min-age')).toHaveValue('48')

      // Turning it off needs no password.
      await page.getByTestId('auto-update-enabled').uncheck()
      await page.getByTestId('auto-update-save').click()
      await expect(toast(page, 'Automatic update settings saved.')).toBeVisible()
      await expect(page.getByTestId('step-up-dialog')).toHaveCount(0)
    } finally {
      await api('/api/admin/settings/auto-update', {
        method: 'PUT',
        body: JSON.stringify({ ...before, password: PASSWORD }),
      })
    }
  })
})

test.describe('scheduled tasks', () => {
  const TASK = 'cleanup_read_notifications'

  test('a task is rescheduled, switched to a daily time, and put back', async ({ admin: page }) => {
    const before = (
      await api<{ items: Array<Record<string, unknown>> }>('/api/admin/crons')
    ).items.find((i) => i.name === TASK)!
    try {
      await page.goto('/admin/scheduled-tasks')
      const kind = page.getByRole('combobox', { name: `${TASK}: Schedule` })
      await kind.selectOption('interval')
      await page.getByRole('spinbutton', { name: `${TASK}: Every (min)` }).fill('720')
      const r = page.locator('tr', { has: kind })
      await r.getByRole('button', { name: 'Save' }).click()
      await expect(toast(page, 'Schedule saved')).toBeVisible()
      await page.reload()
      await expect(page.getByRole('spinbutton', { name: `${TASK}: Every (min)` })).toHaveValue(
        '720',
      )

      await kind.selectOption('daily')
      await page.getByLabel(`${TASK}: Daily at`).fill('04:15')
      await r.getByRole('button', { name: 'Save' }).click()
      await expect(toast(page, 'Schedule saved')).toBeVisible()
      await page.reload()
      await expect(page.getByLabel(`${TASK}: Daily at`)).toHaveValue('04:15')

      // Disabled, the next run reads "disabled".
      await r.locator('input[type=checkbox]').first().uncheck()
      await r.getByRole('button', { name: 'Save' }).click()
      await expect(toast(page, 'Schedule saved')).toBeVisible()
      await expect(r.locator('.next')).toHaveText('disabled')
    } finally {
      await api(`/api/admin/crons/${TASK}`, {
        method: 'PUT',
        body: JSON.stringify({
          enabled: before.enabled,
          kind: before.kind,
          interval_minutes: before.interval_minutes,
          daily_time: before.daily_time,
          alert_on_failure: before.alert_on_failure,
        }),
      })
    }
  })

  test('run now queues a task and its run is recorded', async ({ admin: page }) => {
    await page.goto('/admin/scheduled-tasks')
    const kind = page.getByRole('combobox', { name: `${TASK}: Schedule` })
    const r = page.locator('tr', { has: kind })
    const okBefore = Number(await r.locator('.counts .ok').textContent())
    await r.getByRole('button', { name: 'Run now' }).click()
    await expect(toast(page, `${TASK} queued`)).toBeVisible()
    await expect
      .poll(
        async () => {
          const items = (
            await api<{ items: Array<{ name: string; last_24h: { success: number } }> }>(
              '/api/admin/crons',
            )
          ).items
          return items.find((i) => i.name === TASK)!.last_24h.success
        },
        { timeout: 30_000 },
      )
      .toBeGreaterThan(okBefore)
  })
})

test.describe('backup & restore', () => {
  test('export with a passphrase, preview the file, import it', async ({ admin: page }) => {
    await page.goto('/admin/settings/backup')
    // Only settings: no users or groups are touched by the import below.
    for (const c of [
      'OIDC providers & webhooks',
      'Groups & memberships',
      'User accounts',
      'Logs',
    ]) {
      await page.getByRole('checkbox', { name: new RegExp(`^${c}`) }).uncheck()
    }
    await page.getByRole('checkbox', { name: /^Settings & branding/ }).check()
    await page.getByRole('radio', { name: /^Passphrase-encrypted/ }).check()
    const pass = 'e2e backup passphrase 1234'
    await page.getByRole('textbox', { name: /^At least/ }).fill(pass)
    await page.getByRole('textbox', { name: 'Repeat passphrase' }).fill('different')
    await expect(page.getByText(/Passphrases must match/)).toBeVisible()
    await page.getByRole('textbox', { name: 'Repeat passphrase' }).fill(pass)

    const dl = page.waitForEvent('download')
    await page.getByTestId('backup-export').click()
    await stepUp(page, PASSWORD)
    const file = await (await dl).path()
    await expect(toast(page, 'Backup downloaded.')).toBeVisible()
    expect(JSON.parse(readFileSync(file, 'utf8'))).toBeTruthy()

    await page.getByLabel('Backup file').setInputFiles(file)
    await page.getByRole('textbox', { name: /^Passphrase \(only if/ }).fill(pass)
    await page.getByRole('button', { name: 'Preview changes' }).click()
    await expect(
      page.getByRole('heading', { name: 'Preview - nothing has changed yet' }),
    ).toBeVisible()
    await expect(page.getByText(/^Categories: settings_branding/)).toBeVisible()

    await page.getByTestId('backup-import').click()
    await stepUp(page, PASSWORD)
    await expect(page.getByRole('heading', { name: 'Import complete' })).toBeVisible({
      timeout: 30_000,
    })
  })

  test('a file that is not a backup is refused in the preview', async ({ admin: page }) => {
    await page.goto('/admin/settings/backup')
    await page.getByLabel('Backup file').setInputFiles({
      name: 'not-a-backup.json',
      mimeType: 'application/json',
      buffer: Buffer.from('{"hello": "world"}'),
    })
    await page.getByRole('button', { name: 'Preview changes' }).click()
    await expect(page.locator('[role="alert"]').first()).toBeVisible()
  })
})

test.describe('encryption at rest (page)', () => {
  test('state, counts and the custody acknowledgement gate are shown', async ({ admin: page }) => {
    await page.goto('/admin/settings/encryption')
    await expect(page.getByTestId('encryption-status')).toBeVisible()
    await expect(page.getByTestId('encryption-files-plaintext')).toBeVisible()
    await expect(page.getByTestId('encryption-custody')).toBeVisible()
    const on = page.getByTestId('encryption-turn-on')
    if (await on.count()) {
      await expect(on).toBeDisabled()
      await page.getByTestId('encryption-ack').check()
      await expect(on).toBeEnabled()
      // Cancelling the password prompt changes nothing.
      await on.click()
      await page.getByTestId('step-up-dialog').getByRole('button', { name: 'Cancel' }).click()
      await expect(page.getByTestId('encryption-state')).toHaveText(/off/i)
    }
  })
})
