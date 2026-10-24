import { BASE } from '../../helpers'
import { api, expect, newUser, test, toast, uid, uploadFile, type Person } from './_admin'

/* The policy pages with their own forms: share approval, public links (and
 * recipients without an account), 2FA enforcement + passkey sign-in, email
 * change, secrets, quarantine alerts, errors & alerts, maintenance, share
 * defaults + file preview, the scan guard. Each change is saved, survives a
 * reload, shows its effect where that is cheap to observe, and the page's
 * settings are put back exactly as they were. */

type Json = Record<string, unknown>

/** Takes a settings document and returns a function that PUTs it back and
 * checks it really is back. The update models ignore unknown keys, so the
 * GET body is a valid PUT body. */
async function snapshot(path: string, toBody: (s: Json) => Json = (s) => s) {
  const before = await api<Json>(path)
  return async () => {
    await api(path, { method: 'PUT', body: JSON.stringify(toBody(before)) })
    // Saving once makes the stored policy win over the env default for good.
    const strip = ({ is_kv_overridden: _, ...rest }: Json) => rest
    expect(strip(await api<Json>(path))).toEqual(strip(before))
  }
}

async function tokenOf(p: Person): Promise<string> {
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email: p.email, password: p.password }),
  })
  return (await r.json()).access_token as string
}

function newShare(token: string, body: Json) {
  return fetch(`${BASE}/api/shares`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
    body: JSON.stringify({
      kind: 'outbound',
      recipients: { user_ids: [], group_ids: [] },
      expires_at: null,
      subject: `Policy check ${uid()}`,
      ...body,
    }),
  })
}

test('share approval: on, every share waits for an approver', async ({ admin: page }) => {
  const restore = await snapshot('/api/admin/settings/share-approval')
  try {
    await page.goto('/admin/settings/share-approval')
    await page.getByRole('checkbox', { name: 'Require approval for new shares' }).check()
    await page.getByRole('radio', { name: /^Admins only/ }).check()
    await page.getByRole('combobox', { name: 'Which shares need approval' }).selectOption('all')
    await page.getByRole('checkbox', { name: 'Approvers can review file contents' }).check()
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'Share-approval policy saved.')).toBeVisible()
    await page.reload()
    await expect(
      page.getByRole('checkbox', { name: 'Require approval for new shares' }),
    ).toBeChecked()
    await expect(page.getByRole('combobox', { name: 'Which shares need approval' })).toHaveValue(
      'all',
    )

    const emp = await newUser('employee', 'approval')
    // An employee reaches another employee freely (clients only when connected).
    const colleague = await newUser('employee', 'approval-rcpt')
    const r = await newShare(await tokenOf(emp), {
      recipients: { user_ids: [colleague.id], group_ids: [] },
    })
    expect(r.status).toBe(201)
    expect((await r.json()).state).toBe('pending_approval')
  } finally {
    await restore()
  }
})

test('share approval: the inert combination is called out', async ({ admin: page }) => {
  const restore = await snapshot('/api/admin/settings/share-approval')
  try {
    await page.goto('/admin/settings/share-approval')
    await page.getByRole('checkbox', { name: 'Require approval for new shares' }).check()
    await page.getByRole('radio', { name: /^Employees and admins/ }).check()
    await page.getByRole('checkbox', { name: "Approvers' own shares skip approval" }).check()
    await page
      .getByRole('combobox', { name: 'Which shares need approval' })
      .selectOption('outbound')
    await expect(page.getByText(/approves everything automatically/)).toBeVisible()
  } finally {
    await restore()
  }
})

test('public links: admins only, with an allowlisted employee; outside recipients on', async ({
  admin: page,
}) => {
  const restore = await snapshot('/api/admin/settings/public-links/policy')
  const allowed = await newUser('employee', 'link-allowed')
  const denied = await newUser('employee', 'link-denied')
  try {
    await page.goto('/admin/settings/public-links')
    await page.getByRole('radio', { name: /^Admins only/ }).check()
    await page.getByRole('searchbox', { name: 'Search by name or email…' }).fill(allowed.email)
    await page.locator('.user-suggest', { hasText: allowed.email }).click()
    await page.getByTestId('external-enabled').check()
    await page.getByTestId('external-offer-invite').check()
    await page.locator('form.policy-form').getByRole('button', { name: 'Save' }).first().click()
    await expect(toast(page, 'Public link policy saved.')).toBeVisible()
    await page.reload()
    await expect(page.getByRole('radio', { name: /^Admins only/ })).toBeChecked()
    await expect(page.getByTestId('external-offer-invite')).toBeChecked()

    const link = { public_link: { password: null, expires_at: null, max_downloads: null } }
    expect((await newShare(await tokenOf(allowed), link)).status).toBe(201)
    expect((await newShare(await tokenOf(denied), link)).status).toBe(403)
  } finally {
    await restore()
  }
})

test('2FA enforcement: a required role is enforced; passkey sign-in switches', async ({
  admin: page,
}) => {
  const restore = await snapshot('/api/admin/settings/twofa')
  try {
    const group = await api<{ id: number; name: string }>('/api/groups', {
      method: 'POST',
      body: JSON.stringify({ name: `2FA group ${uid()}`, description: '' }),
    })
    const member = await newUser('employee', 'twofa-member')
    await api(`/api/groups/${group.id}/members`, {
      method: 'POST',
      body: JSON.stringify({ user_ids: [member.id] }),
    })
    await page.goto('/admin/settings/twofa')
    await page.getByRole('checkbox', { name: 'Clients' }).check()
    await page.getByRole('checkbox', { name: group.name }).check()
    await page.getByTestId('passkey-signin-switch').check()
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, '2FA enforcement policy saved.')).toBeVisible()
    await page.reload()
    await expect(page.getByRole('checkbox', { name: 'Clients' })).toBeChecked()
    await expect(page.getByTestId('passkey-signin-switch')).toBeChecked()

    const c = await newUser('client', 'twofa')
    const u = await api<{ requires_2fa: boolean }>(`/api/admin/users/${c.id}`)
    expect(u.requires_2fa).toBe(true)
    // ...and so must the member of a required group, whatever their role.
    expect(
      (await api<{ requires_2fa: boolean }>(`/api/admin/users/${member.id}`)).requires_2fa,
    ).toBe(true)
    // The sign-in page now offers a passkey.
    const guest = await (await page.context().browser()!.newContext({ baseURL: BASE })).newPage()
    await guest.goto('/login')
    await expect(guest.getByRole('button', { name: 'Sign in with a passkey' })).toBeVisible()
    await guest.context().close()
  } finally {
    await restore()
  }
})

test('email change: verification, SSO handling and self-service are saved', async ({
  admin: page,
}) => {
  const restore = await snapshot('/api/admin/settings/email-change')
  try {
    await page.goto('/admin/settings/email-change')
    await page.getByRole('radio', { name: /^Confirm via both addresses/ }).check()
    await page.getByRole('radio', { name: /^Keep the SSO link/ }).check()
    const self = page.getByRole('checkbox', { name: 'Let users change their own email' })
    const was = await self.isChecked()
    await self.setChecked(!was)
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'Email-change policy saved.')).toBeVisible()
    await page.reload()
    await expect(page.getByRole('radio', { name: /^Confirm via both addresses/ })).toBeChecked()
    await expect(page.getByRole('radio', { name: /^Keep the SSO link/ })).toBeChecked()
    await expect(self).toBeChecked({ checked: !was })
  } finally {
    await restore()
  }
})

test('secrets policy: switched on and off, failure mode saved', async ({ admin: page }) => {
  const restore = await snapshot('/api/admin/settings/secrets')
  try {
    await page.goto('/admin/settings/secrets')
    const on = page.getByTestId('secrets-enabled')
    const was = await on.isChecked()
    await on.setChecked(!was)
    await page.getByRole('radio', { name: /^Destroy after too many/ }).check()
    await page
      .locator('form.policy-form > .actions, form.policy-form .actions')
      .first()
      .getByRole('button', { name: 'Save' })
      .click()
    await expect(toast(page, 'Secrets policy saved.')).toBeVisible()
    await page.reload()
    await expect(on).toBeChecked({ checked: !was })
    await expect(page.getByRole('radio', { name: /^Destroy after too many/ })).toBeChecked()
  } finally {
    await restore()
  }
})

test('quarantine alerts: the admin fan-out is saved; the AV panel reports dev mode', async ({
  admin: page,
}) => {
  const before = await api<{ notify_admins: boolean }>('/api/admin/settings/quarantine')
  try {
    await page.goto('/admin/settings/quarantine')
    const toggle = page.getByRole('checkbox', {
      name: 'Notify all admins when a virus is detected',
    })
    await toggle.setChecked(!before.notify_admins)
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'Quarantine alert setting saved.')).toBeVisible()
    await page.reload()
    await expect(toggle).toBeChecked({ checked: !before.notify_admins })
    // The e2e stack runs with AV_SKIP: no engine to reload.
    await expect(
      page.getByText('Running in AV_SKIP dev mode - no ClamAV engine attached.'),
    ).toBeVisible()
  } finally {
    await api('/api/admin/settings/quarantine', {
      method: 'PUT',
      body: JSON.stringify({ notify_admins: before.notify_admins }),
    })
  }
})

test('errors & alerts: logging, 4xx capture and alert recipients are saved', async ({
  admin: page,
}) => {
  const restore = await snapshot('/api/admin/settings/error-alerts')
  try {
    await page.goto('/admin/settings/error-alerts')
    await page.getByRole('checkbox', { name: 'Also record selected client errors (4xx)' }).check()
    await page.getByPlaceholder('429, 409').fill('429')
    await page.getByRole('checkbox', { name: 'Email admins about errors' }).check()
    await page.getByRole('radio', { name: 'A specific list of addresses' }).check()
    await page.locator('textarea').first().fill('ops@e2e.example')
    await page.locator('form.policy-form').getByRole('button', { name: 'Save' }).first().click()
    await expect(toast(page, /saved/i)).toBeVisible()
    await page.reload()
    await expect(page.getByRole('checkbox', { name: 'Email admins about errors' })).toBeChecked()
    await expect(page.locator('textarea').first()).toHaveValue(/ops@e2e\.example/)
    await page.getByRole('link', { name: 'Open the error log' }).click()
    await expect(page).toHaveURL(/\/admin\/error-log$/)
  } finally {
    await restore()
  }
})

test('maintenance mode: transfers pause with the banner, then resume', async ({ admin: page }) => {
  const emp = await newUser('employee', 'maint')
  const token = await tokenOf(emp)
  const message = `Back shortly ${uid()}`
  try {
    await page.goto('/admin/settings/maintenance')
    await page.getByRole('checkbox', { name: 'Enable maintenance mode' }).check()
    await page.getByRole('textbox', { name: /^Banner message/ }).fill(message)
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'Maintenance settings saved.')).toBeVisible()
    await expect(page.getByText(message).first()).toBeVisible()
    await expect(uploadFile(token, 'during-maintenance.txt')).rejects.toThrow(/503/)

    await page.getByRole('checkbox', { name: 'Enable maintenance mode' }).uncheck()
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'Maintenance settings saved.')).toBeVisible()
    await uploadFile(token, 'after-maintenance.txt')
  } finally {
    await api('/api/admin/settings/maintenance', {
      method: 'PUT',
      body: JSON.stringify({ enabled: false, message: '' }),
    })
  }
})

test('files & transfers: share defaults and file preview are saved', async ({ admin: page }) => {
  const restoreDefaults = await snapshot('/api/admin/settings/share-defaults')
  const restorePreview = await snapshot('/api/admin/settings/file-preview')
  try {
    await page.goto('/admin/settings/transfers')
    const notify = page.getByRole('checkbox', { name: /Notify recipients by default/ })
    const preview = page.getByRole('checkbox', { name: 'Enable in-browser file preview' })
    const [n0, p0] = [await notify.isChecked(), await preview.isChecked()]
    await notify.setChecked(!n0)
    await page.locator('form', { has: notify }).getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'Share defaults saved.')).toBeVisible()
    await preview.setChecked(!p0)
    await page.locator('form', { has: preview }).getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'File preview setting saved.')).toBeVisible()
    await page.reload()
    await expect(notify).toBeChecked({ checked: !n0 })
    await expect(preview).toBeChecked({ checked: !p0 })
  } finally {
    await restoreDefaults()
    await restorePreview()
  }
})

test('scan guard: switched on with its thresholds, a contradiction is refused', async ({
  admin: page,
}) => {
  const restore = await snapshot('/api/admin/scan-guard')
  try {
    await page.goto('/admin/settings/scan-guard')
    await page.getByRole('checkbox', { name: 'Enable the scan guard' }).check()
    await page.getByRole('spinbutton', { name: 'First block (minutes)' }).fill('30')
    await page.getByRole('spinbutton', { name: 'Longest block (minutes)' }).fill('10')
    await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled()
    await expect(page.locator('.fh-notice[data-tone="warning"]').first()).toBeVisible()
    await page.getByRole('spinbutton', { name: 'Longest block (minutes)' }).fill('120')
    await page.getByRole('radio', { name: /^Every block/ }).check()
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, /Scan guard settings/)).toBeVisible()
    await page.reload()
    await expect(page.getByRole('checkbox', { name: 'Enable the scan guard' })).toBeChecked()
    await expect(page.getByRole('spinbutton', { name: 'First block (minutes)' })).toHaveValue('30')
    await page
      .getByRole('link', { name: /blocked sources|manage/i })
      .first()
      .click()
    await expect(page).toHaveURL(/\/admin\/ip-blocks$/)
  } finally {
    await restore()
  }
})
