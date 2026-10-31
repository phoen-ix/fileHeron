import { readFileSync } from 'node:fs'

import { api, confirm, expect, ingestMail, test, toast, uid } from './_admin'

/* Email: outgoing SMTP settings, the template editor, inbound IMAP settings
 * and the admin Inbox. The e2e stack has no mail server: SMTP stays unset
 * (mail is printed, which the other journeys rely on), so a save is undone
 * at once and a connection test reports its failure; inbox messages arrive
 * through the app's own ingest (see ingestMail). */

test.describe('outgoing mail (SMTP)', () => {
  test('settings are saved and cleared again; credentials or "anonymous" are required', async ({
    admin: page,
  }) => {
    const before = await api<Record<string, unknown>>('/api/admin/settings/email')
    try {
      await page.goto('/admin/settings/email')
      await expect(page.getByText('Logs fallback (no host set)')).toBeVisible()
      await page.getByRole('textbox', { name: /^Host/ }).fill('smtp.e2e.example')
      await page.getByRole('spinbutton', { name: /^Port/ }).fill('2525')
      await page.getByRole('radio', { name: /^None \(insecure\)/ }).check()
      const save = page.locator('form.email-form').getByRole('button', { name: 'Save' })
      await expect(save).toBeDisabled()
      await expect(page.getByText(/Enter an SMTP username and password/)).toBeVisible()
      await page.getByRole('checkbox', { name: /Allow no authentication/ }).check()
      await page.getByRole('textbox', { name: /^From email/ }).fill('files@e2e.example')
      await page.getByRole('textbox', { name: /^From name/ }).fill('E2E Files')
      await save.click()
      await expect(toast(page, 'SMTP settings saved.')).toBeVisible()
      await expect(page.getByText('SMTP configured')).toBeVisible()
      await page.reload()
      await expect(page.getByRole('textbox', { name: /^Host/ })).toHaveValue('smtp.e2e.example')

      // The test mail goes nowhere: the failure is reported in the page.
      await page.getByPlaceholder('Recipient email for the test').fill('ops@e2e.example')
      await page.getByRole('button', { name: 'Send test email' }).click()
      await expect(page.getByText('Test email failed.')).toBeVisible({
        timeout: 30_000,
      })
    } finally {
      await api('/api/admin/settings/email', {
        method: 'PUT',
        body: JSON.stringify({
          host: '',
          port: before.port ?? 587,
          user: '',
          password: '',
          from_email: before.from_email ?? '',
          from_name: before.from_name ?? '',
          tls_mode: before.tls_mode ?? 'starttls',
          helo_hostname: '',
        }),
      })
    }
    expect((await api<{ host: string | null }>('/api/admin/settings/email')).host || '').toBe('')
  })
})

test.describe('email templates', () => {
  test('edit a template per language, preview it, reset it to the default', async ({
    admin: page,
  }) => {
    const s = uid()
    const subject = `Your invitation ${s}`
    const subjectDe = `Ihre Einladung ${s}`
    const marker = `Marker ${s}:`
    const subjectField = page.getByRole('textbox', { name: 'Subject' })
    const pill = page.locator('.editor-head .fh-pill')
    const frame = page.getByTitle('Preview', { exact: true })
    const text = page.locator('pre.preview-text')
    try {
      await page.goto('/admin/settings/email-templates')
      await page
        .getByRole('navigation', { name: 'Templates' })
        .getByRole('button', { name: 'Invitation' })
        .click()
      await expect(page.getByRole('heading', { name: 'Invitation', level: 2 })).toBeVisible()

      // English: subject, and a placeholder inserted at the cursor of the body.
      await subjectField.fill(subject)
      const body = page.getByRole('textbox', { name: 'Message body' })
      await body.click()
      await page.keyboard.press('ControlOrMeta+End')
      await page.keyboard.type(` ${marker} `)
      await page.getByText('Available placeholders').click()
      await page.getByRole('button', { name: '[RECIPIENT]', exact: true }).click()
      await expect(body).toContainText(`${marker} [RECIPIENT]`)
      await page.getByRole('button', { name: 'Save' }).click()
      await expect(toast(page, 'Template saved')).toBeVisible()
      await expect(pill).toHaveText('Customised')

      // The preview fills the placeholder with sample data, as HTML and as text.
      await page.getByRole('button', { name: 'Preview' }).click()
      await expect(page.getByRole('heading', { name: 'Preview', level: 2 })).toBeVisible()
      await expect(page.locator('.preview-subject')).toContainText(subject)
      await expect(frame.contentFrame().locator('body')).toContainText(`${marker} Grace Hopper`)
      await expect(text).toHaveCount(0)
      await page.getByRole('button', { name: 'Plain text' }).click()
      await expect(text).toContainText(`${marker} Grace Hopper`)
      await expect(frame).toHaveCount(0)
      await page.getByRole('button', { name: 'HTML', exact: true }).click()
      await expect(frame).toBeVisible()
      await expect(text).toHaveCount(0)

      // The saved subject is what an invitation now carries.
      const email = `tpl-${s}@e2e.local`
      await api('/api/account/invite', {
        method: 'POST',
        body: JSON.stringify({
          email,
          target_role: 'client',
          display_name_hint: 'Tpl',
        }),
      })
      await expect
        .poll(async () =>
          JSON.stringify(
            await api(`/api/admin/mail-log?recipient_email=${encodeURIComponent(email)}`),
          ),
        )
        .toContain(subject)

      // The e2e stack has no SMTP host: the test send says so.
      await page.getByRole('button', { name: 'Send test to me' }).click()
      await expect(toast(page, 'SMTP host is empty')).toBeVisible()

      // German: its own override, the English one untouched.
      await page.getByRole('tab', { name: 'Deutsch' }).click()
      await expect(page.getByRole('tab', { name: 'Deutsch' })).toHaveAttribute(
        'aria-selected',
        'true',
      )
      await expect(pill).toHaveText('Default')
      await subjectField.fill(subjectDe)
      await page.getByRole('button', { name: 'Save' }).click()
      await expect(toast(page, 'Template saved')).toBeVisible()
      await expect(pill).toHaveText('Customised')
      await page.getByRole('button', { name: 'Preview' }).click()
      await expect(page.locator('.preview-subject')).toContainText(subjectDe)
      await page.getByRole('tab', { name: 'English' }).click()
      await expect(subjectField).toHaveValue(subject)

      await page.getByRole('button', { name: 'Reset to default' }).click()
      await confirm(page)
      await expect(toast(page, 'Reverted to the built-in default')).toBeVisible()
      await expect(pill).toHaveText('Default')
      await expect(subjectField).not.toHaveValue(subject)
    } finally {
      for (const loc of ['en', 'de']) {
        await api(`/api/admin/settings/email-templates/invite/${loc}`, {
          method: 'DELETE',
        }).catch(() => undefined)
      }
    }
  })

  test('switching away from unsaved changes asks first', async ({ admin: page }) => {
    await page.goto('/admin/settings/email-templates')
    await page
      .getByRole('navigation', { name: 'Templates' })
      .getByRole('button', { name: 'Password reset' })
      .click()
    await page.getByRole('textbox', { name: 'Subject' }).fill(`Unsaved ${uid()}`)
    await page
      .getByRole('navigation', { name: 'Templates' })
      .getByRole('button', { name: 'Invitation' })
      .click()
    await expect(page.getByRole('dialog')).toContainText('Discard your unsaved changes')
    await confirm(page, false)
    await expect(page.getByRole('heading', { name: 'Password reset', level: 2 })).toBeVisible()
    await page
      .getByRole('navigation', { name: 'Templates' })
      .getByRole('button', { name: 'Invitation' })
      .click()
    await confirm(page)
    await expect(page.getByRole('heading', { name: 'Invitation', level: 2 })).toBeVisible()
  })
})

test.describe('inbound mail (IMAP)', () => {
  test('settings are saved; test and fetch report the unreachable server', async ({
    admin: page,
  }) => {
    const before = await api<Record<string, unknown>>('/api/admin/settings/imap')
    try {
      await page.goto('/admin/settings/imap')
      await page.getByRole('checkbox', { name: 'Enable inbound mail fetching' }).check()
      await page.getByRole('checkbox', { name: /Use my outgoing email/ }).uncheck()
      await page.getByRole('textbox', { name: 'IMAP host' }).fill('imap.e2e.example')
      await page.getByRole('combobox', { name: 'Encryption' }).selectOption('none')
      await page.getByRole('textbox', { name: 'Username' }).fill('inbox@e2e.example')
      await page.getByLabel(/^Password/).fill('e2e-not-a-secret')
      await page.getByRole('combobox', { name: /After a message is fetched/ }).selectOption('move')
      await page.getByRole('textbox', { name: 'Target subfolder' }).fill('Done')
      await page.getByRole('combobox', { name: 'Notify admins of new mail' }).selectOption('human')
      await page.getByRole('button', { name: 'Save' }).click()
      await expect(toast(page, 'Inbound mail settings saved')).toBeVisible()
      await page.reload()
      await expect(page.getByRole('textbox', { name: 'IMAP host' })).toHaveValue('imap.e2e.example')
      await expect(page.getByRole('combobox', { name: /After a message is fetched/ })).toHaveValue(
        'move',
      )

      await page.getByRole('button', { name: 'Test connection' }).click()
      await expect(page.getByText('Connection failed')).toBeVisible({
        timeout: 30_000,
      })
      await page.getByRole('button', { name: 'Fetch now' }).click()
      // The connection error comes back as a warning toast.
      await expect(page.locator('.toast[data-tone="warn"]').first()).toBeVisible({
        timeout: 30_000,
      })
    } finally {
      await api('/api/admin/settings/imap', {
        method: 'PUT',
        body: JSON.stringify({ ...before, enabled: false, password: null }),
      })
    }
  })
})

test.describe('admin inbox', () => {
  test('list, search, open, read the attachment, archive, delete', async ({ admin: page }) => {
    const subject = `Inbound ${uid()}`
    const other = `Other ${uid()}`
    ingestMail(subject)
    ingestMail(other)
    await page.goto('/admin/inbox')
    // The sidebar counts unread mail.
    await expect(page.locator('.admin-sidebar .nav-badge').first()).toHaveText(/^\d+$/)
    await page.getByRole('searchbox').fill(subject)
    await page.getByRole('searchbox').press('Enter')
    const rows = page.locator('tr.row')
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toHaveClass(/unread/)
    await page.getByRole('button', { name: `Open message: ${subject}` }).press('Enter')
    await expect(page).toHaveURL(/\/admin\/inbox\/\d+$/)
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(subject)

    await page.getByRole('button', { name: 'Plain text' }).click()
    await expect(page.locator('pre.body-text')).toContainText('Hello from outside.')
    await page.getByRole('button', { name: 'HTML', exact: true }).click()

    const att = page.locator('.att', { hasText: 'note.txt' })
    await expect(att).toBeVisible()
    const dl = att.getByRole('button', { name: 'Download' })
    if (await dl.count()) {
      const d = page.waitForEvent('download')
      await dl.click()
      expect(readFileSync(await (await d).path(), 'utf8')).toContain('attached text')
    } else {
      await expect(att.locator('.att-state')).toBeVisible()
    }

    await page.getByRole('button', { name: 'Archive' }).click()
    await expect(toast(page, 'Updated')).toBeVisible()
    await page.goto('/admin/inbox')
    await page.getByRole('combobox', { name: 'Filter' }).nth(1).selectOption('archived')
    await page.getByRole('searchbox').fill(subject)
    await page.getByRole('searchbox').press('Enter')
    await expect(rows).toHaveCount(1)
    await rows.first().click()
    await page.getByRole('button', { name: 'Delete' }).click()
    await confirm(page)
    await expect(toast(page, 'Message deleted')).toBeVisible()
    await expect(page).toHaveURL(/\/admin\/inbox$/)
  })

  test('the type and status filters narrow the list', async ({ admin: page }) => {
    const subject = `Filtered ${uid()}`
    ingestMail(subject)
    await page.goto('/admin/inbox')
    const rows = page.locator('tr.row', { hasText: subject })
    await expect(rows).toHaveCount(1)
    await page.getByRole('combobox', { name: 'Filter' }).first().selectOption('bounce')
    await expect(rows).toHaveCount(0)
    await page.getByRole('combobox', { name: 'Filter' }).first().selectOption('normal')
    await expect(rows).toHaveCount(1)
    await page.getByRole('combobox', { name: 'Filter' }).nth(1).selectOption('read')
    await expect(rows).toHaveCount(0)
    await page.getByRole('combobox', { name: 'Filter' }).nth(1).selectOption('new')
    await expect(rows).toHaveCount(1)
  })

  test('fetch now reports that inbound mail is off', async ({ admin: page }) => {
    await page.goto('/admin/inbox')
    await page.getByRole('button', { name: 'Fetch now' }).click()
    await expect(toast(page, 'Skipped: disabled')).toBeVisible()
  })
})
