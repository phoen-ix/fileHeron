import { confirm, expect, heading, test, toast, uid } from './_admin'

/* People › SSO: add a provider, see it listed, edit it, test its discovery
 * (no identity provider is reachable from the e2e stack, so the test reports
 * the failure in the form), delete it. Signing in through a real IdP needs an
 * external account - COVERAGE.b.md. */

test('add, list, edit, test and delete an SSO provider', async ({ admin: page }) => {
  const name = `Corp SSO ${uid()}`
  await page.goto('/admin/settings/sso')
  await page.getByRole('button', { name: /Add provider/ }).click()
  await expect(page).toHaveURL(/\/admin\/settings\/sso\/new$/)
  await expect(heading(page)).toHaveText('New SSO provider')

  await page.getByRole('textbox', { name: /^Display name/ }).fill(name)
  await page.getByRole('combobox', { name: /^Preset/ }).selectOption('custom')
  await page
    .getByRole('textbox', { name: /^Issuer URL/ })
    .fill('https://idp.e2e.example/realms/corp')
  await page.getByRole('textbox', { name: /^Client ID/ }).fill('fileheron')
  await page.getByLabel(/^Client secret/).fill('e2e-not-a-secret')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(toast(page, 'SSO provider created.')).toBeVisible()

  // A new provider is enabled: it is offered on the sign-in page.
  const guestCtx = await page.context().browser()!.newContext({ baseURL: process.env.E2E_BASE_URL })
  const guest = await guestCtx.newPage()
  await guest.goto('/login')
  await expect(guest.getByText(`Sign in with ${name}`)).toBeVisible()

  await page.goto('/admin/settings/sso')
  const row = page.locator('.provider-table tbody tr', { hasText: name })
  await expect(row).toContainText('custom')
  await expect(row).toContainText('Enabled')
  await row.click()
  await expect(page).toHaveURL(/\/admin\/settings\/sso\/[0-9a-f-]{36}$/)
  await expect(heading(page)).toHaveText('Edit SSO provider')
  await expect(page.getByLabel(/^Client secret/)).toHaveAttribute('placeholder', /already set/)

  // Renamed and disabled, it leaves the sign-in page.
  await page.getByRole('textbox', { name: /^Display name/ }).fill(`${name} renamed`)
  await page.getByRole('checkbox', { name: 'Enabled (visible on the login page)' }).uncheck()
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(toast(page, 'SSO provider saved.')).toBeVisible()
  await guest.reload()
  await expect(guest.getByRole('heading', { name: 'Welcome back.' })).toBeVisible()
  await expect(guest.getByText(`Sign in with ${name}`)).toHaveCount(0)
  await guestCtx.close()

  await page.getByRole('button', { name: 'Test connection' }).click()
  await expect(page.getByText('Connection failed.')).toBeVisible()

  await page.getByRole('button', { name: 'Delete provider' }).click()
  await confirm(page)
  await expect(toast(page, 'SSO provider deleted.')).toBeVisible()
  await expect(page).toHaveURL(/\/admin\/settings\/sso$/)
  await expect(page.locator('.provider-table tbody tr', { hasText: name })).toHaveCount(0)
})

test('a new provider needs a client secret; cancel goes back to the list', async ({
  admin: page,
}) => {
  await page.goto('/admin/settings/sso/new')
  await page.getByRole('textbox', { name: /^Display name/ }).fill(`No secret ${uid()}`)
  await page.getByRole('combobox', { name: /^Preset/ }).selectOption('custom')
  await page.getByRole('textbox', { name: /^Issuer URL/ }).fill('https://idp.e2e.example/x')
  await page.getByRole('textbox', { name: /^Client ID/ }).fill('fileheron')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.locator('form.sso-form [role="alert"]')).toContainText(
    'Client secret is required',
  )
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page).toHaveURL(/\/admin\/settings\/sso$/)
})

test('a preset with an issuer template fills the issuer from its fields', async ({
  admin: page,
}) => {
  await page.goto('/admin/settings/sso/new')
  await page.getByRole('combobox', { name: /^Preset/ }).selectOption('keycloak')
  const helpers = page.locator('form.sso-form .fh-field-mono[type="text"]')
  const n = await helpers.count()
  for (let i = 0; i < n; i++) await helpers.nth(i).fill(`part${i}`)
  await expect(page.getByRole('textbox', { name: /^Issuer URL/ })).toHaveValue(/part0/)
})
