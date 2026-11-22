import {
  THE_ADMIN,
  asTokenHolder,
  browserOf,
  confirm,
  everySessionEnded,
  expect,
  given,
  mailed,
  openUser,
  pathOf,
  pickRecipient,
  signIn,
  stepUp,
  test,
  toast,
  tryToSignIn,
  uid,
} from './lib'

/* What an admin connects or hands out, used by whoever it is for
 * (COVERAGE.c.md, U23-U26): a sign-on provider configured in the UI, a secret
 * request answered and opened, an API token minted for someone, and a
 * configuration backup restored over later changes. */

test('U23 a sign-on provider the admin sets up in the UI signs a client in, until the admin switches it off', async ({
  page,
  browser,
}) => {
  const client = await given.user('client', 'viasso')
  const name = `Company login ${uid()}`
  const adminPage = await browserOf(browser, THE_ADMIN)
  await adminPage.goto('/admin/settings/sso')
  await adminPage.getByRole('button', { name: /Add provider/ }).click()
  await adminPage.getByRole('textbox', { name: /^Display name/ }).fill(name)
  await adminPage.getByRole('combobox', { name: /^Preset/ }).selectOption('custom')
  // The stand-in identity provider the e2e stack runs (e2e/oidc/idp.py).
  await adminPage.getByRole('textbox', { name: /^Issuer URL/ }).fill('http://oidc:9000')
  await adminPage.getByRole('textbox', { name: /^Client ID/ }).fill('fileheron-e2e')
  await adminPage.getByLabel(/^Client secret/).fill('e2e-idp-not-a-secret')
  await adminPage.getByRole('button', { name: 'Save' }).click()
  await expect(toast(adminPage, 'SSO provider created.')).toBeVisible()
  await adminPage.goto('/admin/settings/sso')
  await adminPage.locator('.provider-table tbody tr', { hasText: name }).click()
  await adminPage.getByRole('button', { name: 'Test connection' }).click()
  await expect(adminPage.getByText('Connection looks good.')).toBeVisible()

  // The client uses the button the admin's provider put on the sign-in page.
  const viaProvider = page.getByRole('button', { name: `Sign in with ${name}` })
  await page.goto('/login')
  await viaProvider.click()
  await expect(page.getByRole('heading', { name: 'E2E identity provider' })).toBeVisible()
  await page.getByLabel('Email').fill(client.email)
  await page.getByRole('button', { name: 'Sign in at the IdP' }).click()
  await expect(page.getByRole('button', { name: 'Menu' })).toBeVisible()
  await page.goto('/account')
  await expect(page.getByRole('heading', { level: 1, name: client.name })).toBeVisible()
  await expect(page.locator('.oidc-row', { hasText: name }).getByText(/^Connected/)).toBeVisible()

  // Switched off: the button is gone for the next visitor.
  await adminPage.getByRole('checkbox', { name: 'Enabled (visible on the login page)' }).uncheck()
  await adminPage.getByRole('button', { name: 'Save' }).click()
  await expect(toast(adminPage, 'SSO provider saved.')).toBeVisible()
  const guest = await browserOf(browser)
  await guest.goto('/login')
  await expect(guest.getByRole('heading', { name: 'Welcome back.' })).toBeVisible()
  await expect(guest.getByRole('button', { name: `Sign in with ${name}` })).toHaveCount(0)
  await guest.context().close()
  await adminPage.context().close()
})

test('U24 a secret request reaches the person asked by mail; their answer is opened by the one who asked', async ({
  page,
  browser,
}) => {
  const path = '/api/admin/settings/secrets'
  const before = await given.settings(path, { enabled: true })
  try {
    const requester = await given.user('employee', 'asks')
    const asked = await given.user('client', 'answers')
    await given.connected(requester, asked)
    const label = `Router password ${uid()}`
    const answer = `hunter2-${uid()}`

    await signIn(page, requester)
    await page.goto('/secrets')
    await page.getByTestId('request-secret').click()
    await page.getByTestId('request-label').fill(label)
    await page.getByTestId('request-note').fill('The admin one, please.')
    await pickRecipient(page, asked.name)
    await page.getByTestId('request-submit').click()
    // No link to hand over, so the form goes straight to the request's page.
    await expect(page.getByTestId('cancel-request')).toBeVisible()

    // The person asked follows the link in THEIR mail; it asks them to sign in.
    const link = (await mailed(asked.email, /https?:\/\/\S+\/secrets\/requests\/[0-9a-f-]{36}/))[0]
    const theirs = await browserOf(browser)
    await theirs.goto(pathOf(link))
    await theirs.getByLabel('Email', { exact: true }).fill(asked.email)
    await theirs.getByLabel('Password', { exact: true }).fill(asked.password)
    await theirs.getByRole('button', { name: 'Continue' }).click()
    await expect(theirs.getByTestId('request-note-text')).toHaveText('The admin one, please.')
    await theirs.getByTestId('answer-content').fill(answer)
    await theirs.getByTestId('answer-submit').click()
    await expect(theirs.getByTestId('answer-sent')).toBeVisible()

    // The one who asked finds it in their own list and opens the answer.
    await page.goto('/secrets')
    await page.getByTestId('box-requests').click()
    await page.getByTestId('requests-mine').click()
    // "Asked by me" shows every request, the answered one included - it opened
    // on "Open", where this one had just dropped out.
    await page.getByText(label).click()
    await page.getByTestId('open-answer').click()
    await page.getByTestId('reveal-button').click()
    await expect(page.getByTestId('secret-content')).toHaveText(answer)
    await theirs.context().close()
  } finally {
    await given.settingsBack(path, before)
  }
})

test('U25 an API token the admin makes for someone works, stops when switched off, and ends when revoked', async ({
  page,
  browser,
}) => {
  const owner = await given.user('employee', 'automates')
  const name = `backup-script-${uid()}`
  const adminPage = await browserOf(browser, THE_ADMIN)
  await adminPage.goto('/admin/api-tokens')
  await adminPage.getByRole('button', { name: /generate token for user/i }).click()
  await adminPage.getByRole('searchbox').fill(owner.email)
  await adminPage.locator('.user-suggest', { hasText: owner.email }).click()
  await adminPage.getByRole('textbox', { name: /^Token name/ }).fill(name)
  await adminPage.getByRole('radio', { name: /^Full access/ }).check()
  await adminPage.getByRole('button', { name: 'Generate token' }).click()
  await stepUp(adminPage, THE_ADMIN.password)
  // What the admin is shown once is what the script is given.
  const token = (await adminPage.locator('.plaintext-token').innerText()).trim()
  expect(await asTokenHolder(token)).toEqual({ status: 200, email: owner.email })

  // The owner sees the token on their own account page.
  await signIn(page, owner)
  await page.goto('/account')
  const mine = page.locator('#api-tokens .token-row', { hasText: name })
  await expect(mine).toBeVisible()
  await expect(mine.getByTestId('token-disabled')).toHaveCount(0)

  await adminPage.goto('/admin/api-tokens')
  await adminPage.getByRole('searchbox').fill(owner.email)
  const row = adminPage.locator('.token-table tbody tr', { hasText: name })
  await row.getByRole('button', { name: 'Disable' }).click()
  await expect(toast(adminPage, 'Token disabled.')).toBeVisible()
  expect(await asTokenHolder(token)).toEqual({ status: 401, code: 'API_TOKEN_DISABLED' })
  // ... and the owner's page says why their script stopped.
  await page.reload()
  await expect(mine.getByTestId('token-disabled')).toHaveText('Switched off by an administrator')

  await row.getByRole('button', { name: 'Reactivate' }).click()
  await expect(toast(adminPage, 'Token reactivated.')).toBeVisible()
  expect(await asTokenHolder(token)).toEqual({ status: 200, email: owner.email })
  await page.reload()
  await expect(mine).toBeVisible()
  await expect(mine.getByTestId('token-disabled')).toHaveCount(0)

  await row.getByRole('button', { name: 'Revoke' }).click()
  await confirm(adminPage)
  await expect(toast(adminPage, 'Token permanently revoked.')).toBeVisible()
  expect(await asTokenHolder(token)).toEqual({ status: 401, code: 'INVALID_API_TOKEN' })
  await page.reload()
  await expect(page.locator('#api-tokens')).toBeVisible()
  await expect(mine).toHaveCount(0)
  await adminPage.context().close()
})

// A restore REPLACES: what the admin changed after the backup is undone,
// accounts made after it are removed, and everyone is signed out. Last in this
// file - it ends every session on the stack, the helpers' own included.
test('U26 a configuration backup restored over later changes: settings and names are back, later accounts are gone', async ({
  page,
  browser,
}) => {
  test.setTimeout(300_000)
  const banner = '/api/admin/settings/motd'
  const bannerBefore = await given.settings(banner, {})
  const restorer = await given.user('admin', 'restores')
  const kept = await given.user('client', 'kept')
  const s = uid()
  const [textThen, textLater] = [`As backed up ${s}`, `Changed later ${s}`]
  const passphrase = 'use case backup passphrase 1234'
  try {
    const adminPage = await browserOf(browser, restorer)
    const announcement = (p: typeof page) => p.getByRole('note', { name: 'Site announcement' })
    const setBanner = async (text: string) => {
      await adminPage.goto('/admin/settings/general')
      const field = adminPage.getByRole('textbox', { name: /^Banner text/ })
      await adminPage.getByRole('checkbox', { name: /Show the banner on the login page/ }).check()
      await field.fill(text)
      await adminPage.locator('form', { has: field }).getByRole('button', { name: 'Save' }).click()
      await expect(toast(adminPage, 'Login banner saved.')).toBeVisible()
    }

    // The state that gets backed up: a banner, and the admin's own choice of
    // where one alert reaches them (an import used to collide with that row).
    await setBanner(textThen)
    await adminPage.goto('/account')
    const alertChoice = adminPage
      .locator('#notifications')
      .getByRole('combobox', { name: 'Notify me via, for: An update finished', exact: true })
    await alertChoice.selectOption('both')
    await expect(adminPage.locator('#notifications').getByText('Saved.')).toBeVisible()

    await adminPage.goto('/admin/settings/backup')
    for (const c of ['OIDC providers & webhooks', 'Groups & memberships', 'Logs']) {
      await adminPage.getByRole('checkbox', { name: new RegExp(`^${c}`) }).uncheck()
    }
    for (const c of ['Settings & branding', 'User accounts']) {
      await adminPage.getByRole('checkbox', { name: new RegExp(`^${c}`) }).check()
    }
    await adminPage.getByRole('radio', { name: /^Passphrase-encrypted/ }).check()
    await adminPage.getByRole('textbox', { name: /^At least/ }).fill(passphrase)
    await adminPage.getByRole('textbox', { name: 'Repeat passphrase' }).fill(passphrase)
    const downloading = adminPage.waitForEvent('download')
    await adminPage.getByTestId('backup-export').click()
    await stepUp(adminPage, restorer.password)
    const backupFile = await (await downloading).path()
    await expect(toast(adminPage, 'Backup downloaded.')).toBeVisible()

    // Afterwards: the banner changes, someone is renamed, an account is added.
    await setBanner(textLater)
    await openUser(adminPage, kept.email)
    await adminPage.getByLabel('Display name').fill(`Renamed ${s}`)
    await adminPage.getByRole('button', { name: 'Save' }).click()
    await expect(adminPage.getByRole('heading', { level: 1, name: `Renamed ${s}` })).toBeVisible()
    const late = await given.user('client', 'addedlater')

    await page.goto('/login')
    await expect(announcement(page)).toHaveText(textLater)
    const keptPage = await browserOf(browser, kept)
    await keptPage.goto('/account')
    await expect(keptPage.getByRole('heading', { level: 1, name: `Renamed ${s}` })).toBeVisible()

    // The restore: the preview names who will be removed, before anything is.
    await adminPage.goto('/admin/settings/backup')
    await adminPage.getByLabel('Backup file').setInputFiles(backupFile)
    await adminPage.getByRole('textbox', { name: /^Passphrase \(only if/ }).fill(passphrase)
    await adminPage.getByRole('button', { name: 'Preview changes' }).click()
    await expect(adminPage.getByRole('heading', { name: 'Preview - nothing has changed yet' })).toBeVisible()
    await expect(adminPage.locator('li.danger', { hasText: 'Users to be purged' })).toContainText(late.email)
    await adminPage.getByTestId('backup-import').click()
    await stepUp(adminPage, restorer.password)
    await expect(adminPage.getByRole('heading', { name: 'Import complete' })).toBeVisible({ timeout: 120_000 })
    everySessionEnded()

    // A visitor sees the banner as it was backed up.
    await page.reload()
    await expect(announcement(page)).toHaveText(textThen)

    // The renamed person was signed out; they sign in again under their old name.
    await keptPage.goto('/account')
    await expect(keptPage).toHaveURL(/\/login/)
    await signIn(keptPage, kept)
    await keptPage.goto('/account')
    await expect(keptPage.getByRole('heading', { level: 1, name: kept.name })).toBeVisible()

    // The account added after the backup no longer exists.
    await tryToSignIn(page, late.email, late.password)
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page).toHaveURL(/\/login/)

    // The admin who restored signs in again and finds their own choice kept.
    const again = await browserOf(browser, restorer)
    await again.goto('/account')
    await expect(
      again
        .locator('#notifications')
        .getByRole('combobox', { name: 'Notify me via, for: An update finished', exact: true }),
    ).toHaveValue('both')
    for (const p of [again, keptPage, adminPage]) await p.context().close()
  } finally {
    everySessionEnded()
    await given.settingsBack(banner, bannerBefore)
  }
})
