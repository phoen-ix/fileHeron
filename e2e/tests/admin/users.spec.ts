import { BASE, tokenFromStdout } from '../../helpers'
import {
  api,
  confirm,
  expect,
  heading,
  newUser,
  stepUp,
  test,
  toast,
  uid,
  uploadFile,
} from './_admin'

/* /admin/users and /admin/users/:id - inviting, the pending-invite actions,
 * creating an account directly, the filters, and everything on a user's
 * page: profile, lockout, reset link, email change, sessions, files, mails
 * and the right-to-erasure. */

const PASSWORD = 'AdminPass123!'

async function openInviteForm(page: import('@playwright/test').Page) {
  await page.goto('/admin/users')
  await page.getByRole('button', { name: /invite user/i }).click()
  await expect(page.getByRole('heading', { name: 'Invite a new user' })).toBeVisible()
}

function inviteRow(page: import('@playwright/test').Page, email: string) {
  return page.locator('.invites-table tr', { hasText: email })
}

test.describe('invites', () => {
  test('an admin invites a user by email and the invite is listed as pending', async ({
    admin: page,
  }) => {
    const email = `invitee-${uid()}@e2e.local`
    const group = await api<{ id: number; name: string }>('/api/groups', {
      method: 'POST',
      body: JSON.stringify({ name: `Invite group ${uid()}`, description: '' }),
    })
    await openInviteForm(page)
    const send = page.getByRole('button', { name: 'Send invite' })
    await expect(send).toBeDisabled()
    await page.getByRole('textbox', { name: /^Email/ }).fill(email)
    await page.getByRole('textbox', { name: /^Display name/ }).fill('Invited Person')
    await page.getByRole('combobox', { name: /^Role/ }).selectOption('employee')
    await page.getByRole('checkbox', { name: group.name }).check()
    await send.click()
    await expect(toast(page, `Invite sent to`)).toBeVisible()
    const row = inviteRow(page, email)
    await expect(row).toContainText('employee')
    await expect(row).toContainText('pending')
    // The group is applied when the invite is accepted; Details lists it.
    await row.getByRole('button', { name: 'Details' }).click()
    await expect(page.getByRole('dialog', { name: 'Invite details' })).toContainText(
      String(group.id),
    )
    await page
      .getByRole('dialog', { name: 'Invite details' })
      .getByRole('button', { name: 'Close' })
      .click()
    // The invite mail went out (dev printout) with a working register link.
    expect(tokenFromStdout('register')).toBeTruthy()
  })

  test('inviting an address that already has an account is refused', async ({ admin: page }) => {
    const existing = await newUser('client', 'dup')
    await openInviteForm(page)
    await page.getByRole('textbox', { name: /^Email/ }).fill(existing.email)
    await page.getByRole('textbox', { name: /^Display name/ }).fill('Duplicate')
    await page.getByRole('button', { name: 'Send invite' }).click()
    await expect(page.locator('.invite-form [role="alert"]')).toBeVisible()
  })

  test('cancel closes the invite form without inviting', async ({ admin: page }) => {
    await openInviteForm(page)
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByRole('heading', { name: 'Invite a new user' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /invite user/i })).toBeVisible()
  })

  test('details, new link, resend, activate and delete on a pending invite', async ({
    admin: page,
  }) => {
    const a = `inv-a-${uid()}@e2e.local`
    const b = `inv-b-${uid()}@e2e.local`
    for (const email of [a, b]) {
      await api('/api/account/invite', {
        method: 'POST',
        body: JSON.stringify({ email, target_role: 'client', display_name_hint: 'Pending One' }),
      })
    }
    await page.goto('/admin/users')

    // Details
    await inviteRow(page, a).getByRole('button', { name: 'Details' }).click()
    const details = page.getByRole('dialog', { name: 'Invite details' })
    await expect(details).toContainText(a)
    await expect(details).toContainText('client')
    await details.getByRole('button', { name: 'Close' }).click()
    await expect(details).toHaveCount(0)

    // New link: confirm, then the link is shown and can be copied
    await inviteRow(page, a).getByRole('button', { name: 'New link' }).click()
    await confirm(page)
    const link = page.getByLabel('New invite link')
    await expect(link).toHaveValue(/\/register\/[A-Za-z0-9._~-]+$/)
    await page.locator('.fresh-link').getByRole('button', { name: 'Copy' }).click()
    await expect(toast(page, 'Invite link copied')).toBeVisible()
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(await link.inputValue())
    await page.locator('.fresh-link').getByRole('button', { name: 'Done' }).click()
    await expect(link).toHaveCount(0)

    // Resend by email
    await inviteRow(page, a).getByRole('button', { name: 'Resend by email' }).click()
    await expect(toast(page, 'Invite resent')).toBeVisible()

    // Activate: the account exists right away
    await inviteRow(page, a).getByRole('button', { name: 'Activate' }).click()
    const act = page.getByRole('dialog', { name: 'Activate account directly' })
    await act.getByLabel('Display name').fill('Activated Person')
    await act.getByRole('button', { name: 'Activate now' }).click()
    await expect(toast(page, 'Account activated')).toBeVisible()
    await expect(inviteRow(page, a)).toHaveCount(0)
    const users = await api<{ items: Array<{ email: string; display_name: string }> }>(
      `/api/admin/users?q=${encodeURIComponent(a)}`,
    )
    expect(users.items.map((u) => u.display_name)).toEqual(['Activated Person'])

    // Delete
    await inviteRow(page, b).getByRole('button', { name: 'Delete' }).click()
    const del = page.getByRole('dialog', { name: 'Delete invite' })
    await expect(del).toContainText(b)
    await del.getByRole('button', { name: 'Delete' }).click()
    await expect(toast(page, 'Invite deleted')).toBeVisible()
    await expect(inviteRow(page, b)).toHaveCount(0)
  })

  test('an admin creates an account directly with a password', async ({ admin: page, browser }) => {
    const email = `direct-${uid()}@e2e.local`
    const password = `Direct!${uid()}Pw9`
    await openInviteForm(page)
    await page.getByRole('textbox', { name: /^Email/ }).fill(email)
    await page.getByRole('textbox', { name: /^Display name/ }).fill('Direct Person')
    await page.getByRole('checkbox', { name: /^Create the account now/ }).check()
    const create = page.getByRole('button', { name: 'Create user' })
    await page.getByLabel(/^Password/).fill('short')
    await expect(create).toBeDisabled()
    await page.getByLabel(/^Password/).fill(password)
    await create.click()
    await expect(toast(page, `${email} created`)).toBeVisible()
    // ...and it can sign in at once.
    const ctx = await browser.newContext({ baseURL: BASE })
    const r = await ctx.request.post('/api/auth/login', { data: { email, password } })
    expect(r.status()).toBe(200)
    await ctx.close()
  })
})

test('search and the role filter narrow the user list; a row opens the user', async ({
  admin: page,
}) => {
  const s = uid()
  const emp = await newUser('employee', `find-${s}`)
  const cli = await newUser('client', `find-${s}`)
  await page.goto('/admin/users')
  await page.getByRole('searchbox', { name: 'Search by name or email…' }).fill(`find-${s}`)
  const rows = page.locator('.user-table tbody tr')
  await expect(rows).toHaveCount(2)
  await page.getByRole('combobox', { name: 'Filter' }).selectOption('employee')
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText(emp.email)
  await page.getByRole('combobox', { name: 'Filter' }).selectOption('client')
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText(cli.email)
  await page.getByRole('searchbox').fill(`nobody-${s}`)
  await expect(page.getByText('No users match the current filters.')).toBeVisible()
  await page.getByRole('searchbox').fill(cli.email)
  await rows.first().click()
  await expect(page).toHaveURL(new RegExp(`/admin/users/${cli.id}$`))
  await expect(heading(page)).toHaveText(cli.name)
})

test.describe('a user page', () => {
  test('profile: name, role, quota and disabling are saved', async ({ admin: page }) => {
    const u = await newUser('client', 'profile')
    await page.goto(`/admin/users/${u.id}`)
    await page.getByLabel('Display name').fill(`${u.name} renamed`)
    await page.getByLabel('Role').selectOption('employee')
    await page.getByLabel('Quota (bytes)').fill('5000000')
    await page.getByLabel('Disabled (revoke all sessions)').check()
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(heading(page)).toHaveText(`${u.name} renamed`)
    await page.reload()
    await expect(page.getByLabel('Role')).toHaveValue('employee')
    await expect(page.getByLabel('Quota (bytes)')).toHaveValue('5000000')
    await expect(page.locator('.meta-line')).toContainText('disabled')
    // A disabled account cannot sign in.
    const r = await fetch(`${BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: u.email, password: u.password }),
    })
    expect(r.status).toBeGreaterThanOrEqual(400)
    // Re-enable.
    await page.getByLabel('Disabled (revoke all sessions)').uncheck()
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(page.locator('.meta-line')).not.toContainText('disabled')
  })

  test('a locked-out account shows the lock and the admin lifts it', async ({ admin: page }) => {
    const u = await newUser('client', 'locked')
    for (let i = 0; i < 6; i++) {
      // The attempt that locks the account sends the owner a warning. It must
      // not hang: its mail-log row once waited 50 s on this request's own
      // lock of the user row (MariaDB lock wait timeout) and was then lost.
      const started = Date.now()
      await fetch(`${BASE}/api/auth/login`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: u.email, password: 'wrong-password-123' }),
      })
      expect(Date.now() - started, `failed sign-in #${i + 1}`).toBeLessThan(10_000)
    }
    const mails = await api<{ items: Array<{ category: string }> }>(
      `/api/admin/mail-log?recipient_user_id=${u.id}`,
    )
    expect(mails.items.map((m) => m.category)).toContain('lockout_warning')
    await page.goto(`/admin/users/${u.id}`)
    await expect(page.getByTestId('locked-pill')).toBeVisible()
    await page.getByTestId('unlock-btn').click()
    await confirm(page)
    await expect(toast(page, 'Account unlocked.')).toBeVisible()
    await expect(page.getByTestId('locked-pill')).toHaveCount(0)
    const r = await fetch(`${BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: u.email, password: u.password }),
    })
    expect(r.status).toBe(200)
  })

  test('force password reset shows a one-time reset token', async ({ admin: page }) => {
    const u = await newUser('client', 'reset')
    await page.goto(`/admin/users/${u.id}`)
    await page.getByRole('button', { name: 'Force password reset' }).click()
    await confirm(page)
    await expect(page.getByText('Password reset link')).toBeVisible()
    await expect(page.locator('.reset-box .token')).not.toBeEmpty()
  })

  test('change email: applied at once, or as a confirmation link', async ({ admin: page }) => {
    const u = await newUser('client', 'email')
    const now = `now-${uid()}@e2e.local`
    await page.goto(`/admin/users/${u.id}`)
    await page.getByLabel('Change email').fill(now)
    await page.getByLabel('Apply immediately (skip verification)').check()
    await page.getByRole('button', { name: 'Change email' }).click()
    await confirm(page)
    await expect(toast(page, 'Email changed.')).toBeVisible()
    await expect(page.locator('.meta-line')).toContainText(now)

    const later = `later-${uid()}@e2e.local`
    await page.getByLabel('Change email').fill(later)
    await page.getByRole('button', { name: 'Change email' }).click()
    await confirm(page)
    await expect(page.getByText('Confirmation link', { exact: true })).toBeVisible()
    await expect(page.locator('.reset-box .token').first()).toContainText('/confirm-email-change/')
    await expect(page.locator('.meta-line')).toContainText(now)
  })

  test('sessions: one device revoked, then all', async ({ admin: page, browser }) => {
    const u = await newUser('employee', 'sessions')
    const ctxs = []
    for (let i = 0; i < 2; i++) {
      const c = await browser.newContext({ baseURL: BASE })
      await c.request.post('/api/auth/login', { data: { email: u.email, password: u.password } })
      ctxs.push(c)
    }
    await page.goto(`/admin/users/${u.id}`)
    const items = page.locator('.session-item')
    await expect(items).toHaveCount(2)
    await items.first().getByRole('button', { name: 'Revoke' }).click()
    await expect(toast(page, /revoked/i)).toBeVisible()
    await expect(items).toHaveCount(1)
    await page.getByRole('button', { name: 'Revoke all' }).click()
    await confirm(page)
    await expect(page.getByText('No active sessions.')).toBeVisible()
    // The revoked device can no longer refresh.
    expect((await ctxs[1].request.post('/api/auth/refresh')).status()).toBe(401)
    for (const c of ctxs) await c.close()
  })

  test('files: listed, deleted, and linked to File history', async ({ admin: page }) => {
    const u = await newUser('employee', 'files')
    const token = await (async () => {
      const r = await fetch(`${BASE}/api/auth/login`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: u.email, password: u.password }),
      })
      return (await r.json()).access_token as string
    })()
    const name = `user-file-${uid()}.txt`
    await uploadFile(token, name)
    await page.goto(`/admin/users/${u.id}`)
    const item = page.locator('.file-item', { hasText: name })
    await expect(item).toBeVisible()
    await page.getByRole('link', { name: 'View in File History →' }).click()
    await expect(page).toHaveURL(new RegExp(`/admin/file-history\\?uploader_id=${u.id}`))
    await expect(page.locator('td', { hasText: name }).first()).toBeVisible()
    await page.goBack()
    await item.getByRole('button', { name: 'Delete' }).click()
    await confirm(page)
    await expect(page.getByText('No stored files.')).toBeVisible()
  })

  test('emails to the user are listed and open the mail', async ({ admin: page }) => {
    const u = await newUser('client', 'mails')
    // Forgot-password mails the account a reset link.
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
      .toBeGreaterThan(0)
    await page.goto(`/admin/users/${u.id}`)
    await expect(page.locator('.email-link').first()).toContainText('Reset your password')
    await page.locator('.email-link').first().click()
    await expect(page).toHaveURL(/\/admin\/mail-log\/\d+$/)
    await page.goBack()
    await page.getByRole('link', { name: 'View in Mail log →' }).click()
    await expect(page).toHaveURL(new RegExp(`/admin/mail-log\\?recipient_user_id=${u.id}`))
  })

  test('erase: two confirmations, a password, and the account is anonymised', async ({
    admin: page,
  }) => {
    const u = await newUser('employee', 'erase')
    await page.goto(`/admin/users/${u.id}`)
    const erase = page.getByTestId('erase-user')
    await erase.click()
    await expect(page.locator('.erase-preflight')).toBeVisible()
    await expect(erase).toHaveText('I understand - continue')
    await erase.click()
    await stepUp(page, 'not-the-password')
    await expect(page.getByTestId('step-up-dialog').getByRole('alert')).toBeVisible()
    // The receipt PDF is handed over, then the list.
    const receipt = page.waitForEvent('download')
    await stepUp(page, PASSWORD)
    expect((await receipt).suggestedFilename()).toMatch(/^erasure-receipt-\d+\.pdf$/)
    await expect(toast(page, 'User erased')).toBeVisible()
    await expect(page).toHaveURL(/\/admin\/users$/)
    await page.goto(`/admin/users/${u.id}`)
    await expect(page.getByTestId('erase-user')).toHaveText('User already erased')
    await expect(page.getByTestId('erase-user')).toBeDisabled()
    await expect(page.locator('.meta-line')).toContainText('@erased.invalid')
  })
})
