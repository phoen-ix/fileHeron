import { readFileSync } from 'node:fs'

import type { Download, Page } from '@playwright/test'

import { BASE } from '../../helpers'
import { api, confirm, expect, newUser, py, test, toast, uid, uploadFile } from './_admin'

/* The record-keeping pages: audit log, error log, mail log + one mail, and
 * file history - their filters, row details, CSV exports and actions. */

async function csv(page: Page, click: () => Promise<void>, name: string): Promise<string> {
  const dl: Promise<Download> = page.waitForEvent('download')
  await click()
  const d = await dl
  expect(d.suggestedFilename()).toBe(name)
  return readFileSync(await d.path(), 'utf8')
}

async function tokenFor(email: string, password: string): Promise<string> {
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  return (await r.json()).access_token as string
}

test.describe('audit log', () => {
  test('filters by event and target, shows a row in full, exports CSV', async ({ admin: page }) => {
    const g = await api<{ id: number }>('/api/groups', {
      method: 'POST',
      body: JSON.stringify({ name: `Audited ${uid()}`, description: '' }),
    })
    await page.goto('/admin/audit-log')
    await page.getByRole('textbox', { name: /^event_type/ }).fill('group_created')
    await page.getByRole('textbox', { name: 'target_id' }).fill(String(g.id))
    const rows = page.locator('.audit-table tbody tr:not(.extra-row)')
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toContainText('group_created')
    await expect(rows.first()).toContainText(`group:${g.id}`)
    // The actor links to their user page.
    await expect(rows.first().getByRole('link')).toHaveAttribute('href', /\/admin\/users\/\d+$/)
    const toggle = rows.first().getByRole('button', { name: 'Show details' })
    if (await toggle.count()) {
      await toggle.click()
      await expect(page.locator('.extra-row')).toBeVisible()
      await toggle.click()
      await expect(page.locator('.extra-row')).toHaveCount(0)
    }
    await page.getByRole('textbox', { name: 'target_type' }).fill('nothing-has-this-type')
    await expect(rows).toHaveCount(0)

    const body = await csv(
      page,
      () => page.getByRole('button', { name: 'Export CSV' }).click(),
      'audit-log.csv',
    )
    expect(body.split('\n')[0]).toMatch(/event_type/)
  })

  test('the date range narrows the list', async ({ admin: page }) => {
    await page.goto('/admin/audit-log')
    const rows = page.locator('.audit-table tbody tr:not(.extra-row)')
    await expect(rows.first()).toBeVisible()
    await page.getByLabel('to', { exact: true }).fill('2001-01-01T00:00')
    await expect(rows).toHaveCount(0)
    await page.getByLabel('to', { exact: true }).fill('')
    await page.getByLabel('from', { exact: true }).fill('2001-01-01T00:00')
    await expect(rows.first()).toBeVisible()
  })
})

test.describe('error log', () => {
  test('a captured CSP report is listed, filtered, opened and exported', async ({
    admin: page,
  }) => {
    const marker = `https://blocked-${uid()}.example/x.js`
    const r = await fetch(`${BASE}/api/telemetry/csp-report`, {
      method: 'POST',
      headers: { 'content-type': 'application/csp-report' },
      body: JSON.stringify({
        'csp-report': {
          'document-uri': `${BASE}/admin`,
          'violated-directive': 'script-src',
          'effective-directive': 'script-src',
          'blocked-uri': marker,
        },
      }),
    })
    expect(r.status).toBe(204)
    // The worker writes the row.
    await expect
      .poll(async () => JSON.stringify(await api('/api/admin/error-log?source=csp')), {
        timeout: 30_000,
      })
      .toContain(new URL(marker).host)

    await page.goto('/admin/error-log')
    await page.getByRole('combobox', { name: 'Filter' }).selectOption('csp')
    const row = page.locator('.error-table tr.row', { hasText: new URL(marker).host }).first()
    await expect(row).toBeVisible()
    await row.click()
    await expect(page.locator('.detail-row')).toContainText('csp')
    await row.press('Enter')
    await expect(page.locator('.detail-row')).toHaveCount(0)

    await page.getByRole('textbox', { name: /^code/ }).fill(`NO_SUCH_CODE_${uid()}`)
    await expect(page.getByText('No errors match these filters.')).toBeVisible()

    const body = await csv(
      page,
      () => page.getByRole('button', { name: 'Export CSV' }).click(),
      'error-log.csv',
    )
    expect(body.split('\n')[0]).toMatch(/code/)
  })

  test('the IP and status filters narrow the list', async ({ admin: page }) => {
    await page.goto('/admin/error-log')
    await page.getByRole('textbox', { name: 'IP address' }).fill('203.0.113.250')
    await expect(page.getByText('No errors match these filters.')).toBeVisible()
    await page.getByRole('textbox', { name: 'IP address' }).fill('')
    await page.getByRole('spinbutton', { name: /^status/ }).fill('599')
    await expect(page.getByText('No errors match these filters.')).toBeVisible()
  })
})

test.describe('mail log', () => {
  test('search, recipient, category and status filters; a mail opens', async ({ admin: page }) => {
    const u = await newUser('client', 'maillog')
    await fetch(`${BASE}/api/auth/forgot-password`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: u.email }),
    })
    await expect
      .poll(async () =>
        JSON.stringify(
          await api(`/api/admin/mail-log?recipient_email=${encodeURIComponent(u.email)}`),
        ),
      )
      .toContain('reset_password')

    await page.goto('/admin/mail-log')
    const rows = page.locator('.mail-table tbody tr')
    await page.getByRole('textbox', { name: 'recipient email' }).fill(u.email)
    await expect(rows).toHaveCount(1)
    await page.getByRole('textbox', { name: 'category' }).fill('invite')
    await expect(page.getByText('No emails match these filters.')).toBeVisible()
    await page.getByRole('textbox', { name: 'category' }).fill('reset_password')
    await expect(rows).toHaveCount(1)
    await page.getByRole('combobox', { name: 'Filter' }).selectOption('failed')
    await expect(page.getByText('No emails match these filters.')).toBeVisible()
    await page.getByRole('combobox', { name: 'Filter' }).selectOption('sent')
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toContainText('redacted')

    // The recipient links to the user; the subject to the mail.
    await expect(rows.first().getByRole('link', { name: new RegExp(u.name) })).toHaveAttribute(
      'href',
      `/admin/users/${u.id}`,
    )
    await rows.first().getByRole('link', { name: 'Reset your password' }).click()
    await expect(page).toHaveURL(/\/admin\/mail-log\/\d+$/)
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Reset your password')
    await expect(page.locator('.meta')).toContainText(u.email)
    // A mail whose one-time link was redacted cannot be resent.
    await expect(page.getByRole('button', { name: 'Resend' })).toBeDisabled()
    await expect(page.getByText(/can't be resent/)).toBeVisible()
    await expect(page.locator('pre.body-text')).toBeVisible()
    await page
      .getByRole('link', { name: /mail log/i })
      .first()
      .click()
    await expect(page).toHaveURL(/\/admin\/mail-log$/)
  })

  test('the user-scope chip clears the recipient filter; search finds by subject', async ({
    admin: page,
  }) => {
    const u = await newUser('client', 'mailscope')
    await fetch(`${BASE}/api/auth/forgot-password`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: u.email }),
    })
    await expect
      .poll(
        async () =>
          (await api<{ total: number }>(`/api/admin/mail-log?recipient_user_id=${u.id}`)).total,
      )
      .toBe(1)
    await page.goto(`/admin/mail-log?recipient_user_id=${u.id}`)
    const rows = page.locator('.mail-table tbody tr')
    await expect(rows).toHaveCount(1)
    const chip = page.getByRole('button', { name: `Limited to user #${u.id}` })
    await chip.click()
    await expect(chip).toHaveCount(0)
    await expect.poll(() => rows.count()).toBeGreaterThan(1)
    await page.getByRole('textbox', { name: 'Search subject / recipient' }).fill(u.email)
    await expect(rows).toHaveCount(1)

    const body = await csv(
      page,
      () => page.getByRole('button', { name: 'Export CSV' }).click(),
      'mail-log.csv',
    )
    expect(body).toContain(u.email)
  })

  test('a mail with an HTML body shows it sandboxed', async ({ admin: page }) => {
    const u = await newUser('client', 'mailhtml')
    await fetch(`${BASE}/api/auth/forgot-password`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: u.email }),
    })
    let id = 0
    await expect
      .poll(async () => {
        const r = await api<{ items: Array<{ id: number }> }>(
          `/api/admin/mail-log?recipient_user_id=${u.id}`,
        )
        id = r.items[0]?.id ?? 0
        return id
      })
      .toBeGreaterThan(0)
    await page.goto(`/admin/mail-log/${id}`)
    const open = page.getByRole('button', { name: 'Open HTML version' })
    await expect(page.locator('pre.body-text')).toBeVisible()
    await open.click()
    const frame = page.locator('iframe.body-frame')
    await expect(frame).toHaveAttribute('sandbox', '')
    await open.click()
    await expect(frame).toHaveCount(0)
  })
})

test.describe('file history', () => {
  test('search, state filters, sort and delete', async ({ admin: page }) => {
    const u = await newUser('employee', 'history')
    const token = await tokenFor(u.email, u.password)
    const s = uid()
    await uploadFile(token, `alpha-${s}.txt`, 'a')
    await uploadFile(token, `beta-${s}.txt`, 'bbbbbbbbbb')
    await page.goto('/admin/file-history')
    await page.getByRole('searchbox').fill(s)
    const rows = page.locator('.files-table tbody tr')
    await expect(rows).toHaveCount(2)

    // Sort by name, both ways.
    const nameHeader = page.getByRole('button', { name: /^Filename/ })
    await nameHeader.click()
    await expect(nameHeader).toHaveAttribute('aria-sort', /ascending|descending/)
    const first = await rows.first().locator('.row-name').first().textContent()
    await nameHeader.click()
    await expect(rows.first().locator('.row-name').first()).not.toHaveText(first ?? '')
    await page.getByRole('button', { name: /^Size/ }).click()
    await expect(page.getByRole('button', { name: /^Size/ })).toHaveAttribute(
      'aria-sort',
      /ascending|descending/,
    )

    // File-state and share-state filters.
    const [fileState, shareState] = [
      page.getByRole('combobox', { name: 'Filter' }).nth(0),
      page.getByRole('combobox', { name: 'Filter' }).nth(1),
    ]
    await fileState.selectOption('infected')
    await expect(page.getByText('No files match the current filters.')).toBeVisible()
    await fileState.selectOption('')
    await shareState.selectOption('expired')
    await expect(page.getByText('No files match the current filters.')).toBeVisible()
    await shareState.selectOption('active')
    await expect(rows).toHaveCount(2)

    // Delete one.
    const alpha = rows.filter({ hasText: `alpha-${s}.txt` })
    await alpha.getByRole('button', { name: 'Delete' }).click()
    await confirm(page)
    await expect(toast(page, `Deleted “alpha-${s}.txt”.`)).toBeVisible()
    await expect(rows).toHaveCount(1)
    // A deleted file is still in the history with "show deleted" (its
    // one-file share went with it).
    await shareState.selectOption('')
    await page.getByRole('checkbox', { name: 'Show deleted / abandoned' }).check()
    await expect(rows.filter({ hasText: `alpha-${s}.txt` })).toContainText('deleted')
  })

  test('an orphaned file is flagged and reclaimed; the uploader filter clears', async ({
    admin: page,
  }) => {
    const u = await newUser('employee', 'orphan')
    const token = await tokenFor(u.email, u.password)
    const name = `orphan-${uid()}.txt`
    const { shareId } = await uploadFile(token, name, 'orphan bytes')
    // A share revoked with its bytes left behind (what an interrupted
    // purge leaves) - not reachable through the API on purpose.
    py(
      [
        'import sys',
        'from app.database import SessionLocal',
        'from app.models.share import Share, ShareState',
        'db = SessionLocal()',
        'db.get(Share, sys.argv[1]).state = ShareState.revoked',
        'db.commit()',
      ].join('\n'),
      shareId,
    )
    await page.goto(`/admin/file-history?uploader_id=${u.id}`)
    const rows = page.locator('.files-table tbody tr')
    await page.getByRole('checkbox', { name: 'Orphaned only' }).check()
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toContainText('orphaned')
    await rows.first().getByRole('button', { name: 'Reclaim' }).click()
    await confirm(page)
    await expect(toast(page, `Reclaimed “${name}”`)).toBeVisible()
    await page.getByRole('checkbox', { name: 'Orphaned only' }).uncheck()
    const chip = page.getByRole('button', { name: `Filtered to user #${u.id}` })
    await chip.click()
    await expect(chip).toHaveCount(0)
    await expect.poll(() => rows.count()).toBeGreaterThan(1)
  })
})
