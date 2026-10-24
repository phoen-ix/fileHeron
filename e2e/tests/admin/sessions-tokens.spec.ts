import { BASE } from '../../helpers'
import { api, confirm, expect, newUser, stepUp, test, toast, uid } from './_admin'

/* People › Sessions (list + revoke) and People › API tokens (mint on behalf,
 * disable, reactivate, revoke, filters) with its Policy tab. */

const PASSWORD = 'AdminPass123!'

async function login(email: string, password: string) {
  return fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
}

test.describe('sessions', () => {
  test('search, sort, revoke one, revoke all, include the inactive', async ({ admin: page }) => {
    const u = await newUser('client', `sess-${uid()}`)
    for (let i = 0; i < 2; i++) expect((await login(u.email, u.password)).ok).toBeTruthy()
    await page.goto('/admin/sessions')
    await page.getByRole('searchbox').fill(u.email)
    const rows = page.locator('.sessions-table tbody tr')
    await expect(rows).toHaveCount(2)
    await expect(rows.first().getByRole('link', { name: u.name })).toHaveAttribute(
      'href',
      `/admin/users/${u.id}`,
    )
    for (const col of ['Started', 'Last active', 'Expires']) {
      const h = page.getByRole('button', { name: new RegExp(`^${col}`) })
      await h.click()
      await expect(h).toHaveAttribute('aria-sort', /ascending|descending/)
    }

    await rows.first().getByRole('button', { name: 'Revoke', exact: true }).click()
    await confirm(page)
    await expect(toast(page, 'Session revoked.')).toBeVisible()
    await expect(rows).toHaveCount(1)

    await rows.first().getByRole('button', { name: 'Revoke all' }).click()
    await confirm(page)
    await expect(toast(page, /Revoked 1 session/)).toBeVisible()
    await expect(page.getByText('No sessions match the current filters.')).toBeVisible()

    await page.getByRole('checkbox', { name: 'Include expired/revoked' }).check()
    await expect(rows).toHaveCount(2)
    await expect(rows.first()).toContainText('revoked')
  })
})

test.describe('API tokens', () => {
  test('mint a token for a user behind the password; it works', async ({ admin: page }) => {
    const u = await newUser('employee', `tok-${uid()}`)
    const name = `ci-${uid()}`
    await page.goto('/admin/api-tokens')
    await page.getByRole('button', { name: /generate token for user/i }).click()
    const submit = page.getByRole('button', { name: 'Generate token' })
    await expect(submit).toBeDisabled()
    await page.getByRole('searchbox').fill(u.email)
    await page.locator('.user-suggest', { hasText: u.email }).click()
    await expect(page.locator('.picked-user')).toContainText(u.email)
    await page.getByRole('textbox', { name: /^Token name/ }).fill(name)
    await page.getByRole('radio', { name: /^Limited/ }).check()
    await expect(submit).toBeDisabled()
    await page.locator('.scope-group input[type=checkbox]').first().check()
    await page.getByRole('radio', { name: /^Full access/ }).check()
    await submit.click()
    await stepUp(page, 'not-the-password')
    await expect(page.getByTestId('step-up-dialog').getByRole('alert')).toBeVisible()
    await stepUp(page, PASSWORD)
    const plain = page.locator('.plaintext-token')
    await expect(plain).toHaveText(/^fh_[0-9a-f]{8}_/)
    const token = (await plain.textContent())!.trim()
    await page.locator('.plaintext-actions').getByRole('button').first().click()
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(token)
    // The token authenticates as its owner.
    const me = await fetch(`${BASE}/api/account/me`, {
      headers: { authorization: `Bearer ${token}` },
    })
    expect((await me.json()).email).toBe(u.email)
    await page.locator('.plaintext-actions').getByRole('button').last().click()
    await expect(plain).toHaveCount(0)
    await expect(page.locator('.token-table tr', { hasText: name })).toContainText('active')
  })

  test('disable, reactivate and revoke; the status filter follows', async ({ admin: page }) => {
    const u = await newUser('employee', `tok2-${uid()}`)
    await api('/api/admin/api-tokens', {
      method: 'POST',
      body: JSON.stringify({ target_user_id: u.id, name: `life-${uid()}`, password: PASSWORD }),
    })
    await page.goto('/admin/api-tokens')
    await page.getByRole('searchbox').fill(u.email)
    const row = page.locator('.token-table tbody tr', { hasText: u.email })
    await expect(row).toHaveCount(1)

    await row.getByRole('button', { name: 'Disable' }).click()
    await expect(toast(page, 'Token disabled.')).toBeVisible()
    await expect(row).toContainText('disabled')
    await page.getByRole('combobox', { name: 'Filter' }).selectOption('active')
    await expect(page.getByText('No API tokens match the current filters.')).toBeVisible()
    await page.getByRole('combobox', { name: 'Filter' }).selectOption('disabled')
    await expect(row).toHaveCount(1)

    await row.getByRole('button', { name: 'Reactivate' }).click()
    await expect(toast(page, 'Token reactivated.')).toBeVisible()
    await page.getByRole('combobox', { name: 'Filter' }).selectOption('')
    await expect(row).toContainText('active')

    await row.getByRole('button', { name: 'Revoke' }).click()
    await confirm(page)
    await expect(toast(page, 'Token permanently revoked.')).toBeVisible()
    await expect(row).toContainText('revoked')
  })

  test('the policy decides who may mint tokens; the allowlist overrides it', async ({
    admin: page,
  }) => {
    const before = await api<{
      mode: string
      allowed_user_ids: number[]
      allowed_group_ids: number[]
    }>('/api/admin/settings/api-tokens/policy')
    const allowed = await newUser('employee', `allowed-${uid()}`)
    const other = await newUser('employee', `denied-${uid()}`)
    try {
      await page.goto('/admin/settings/api-tokens')
      await page.getByRole('radio', { name: /^Admins only/ }).check()
      await page.getByRole('searchbox', { name: 'Search by name or email…' }).fill(allowed.email)
      await page.locator('.user-suggest', { hasText: allowed.email }).click()
      await expect(page.locator('.picked-row', { hasText: allowed.email })).toBeVisible()
      await page.getByRole('button', { name: 'Save' }).click()
      await expect(toast(page, 'API token policy saved.')).toBeVisible()
      await page.reload()
      await expect(page.getByRole('radio', { name: /^Admins only/ })).toBeChecked()
      await expect(page.locator('.picked-row', { hasText: allowed.email })).toBeVisible()

      for (const [who, status] of [
        [allowed, 201],
        [other, 403],
      ] as const) {
        const token = (await (await login(who.email, who.password)).json()).access_token
        const r = await fetch(`${BASE}/api/account/api-tokens`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
          body: JSON.stringify({ name: 'policy-check', password: who.password }),
        })
        expect(r.status, who.email).toBe(status)
      }

      await page
        .locator('.picked-row', { hasText: allowed.email })
        .getByRole('button', { name: 'Remove' })
        .click()
      await expect(page.locator('.picked-row', { hasText: allowed.email })).toHaveCount(0)
    } finally {
      await api('/api/admin/settings/api-tokens/policy', {
        method: 'PUT',
        body: JSON.stringify({
          mode: before.mode,
          allowed_user_ids: before.allowed_user_ids,
          allowed_group_ids: before.allowed_group_ids,
        }),
      })
    }
  })
})
