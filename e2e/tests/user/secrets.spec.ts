import type { Page } from '@playwright/test'

import { apiFetch } from '../../helpers'
import { admin, connect, expect, newUser, ok, restoreSettings, session, setSettings, signIn, test, uid } from './lib'

/* Secrets and secret requests composed in the UI (the reveal and answer
 * halves: tests/secrets.spec.ts and tests/secret-requests.spec.ts): the
 * create form with its blockers, a link and a passphrase, the three boxes and
 * the state filter, the sender's link management and Burn now, cancelling a
 * request, and a link secret burned by wrong passphrases. The feature ships
 * off; on for this file only. */

const confirmDialog = (page: Page) => page.getByRole('dialog').getByRole('button').last()

async function pick(page: Page, name: string): Promise<void> {
  const box = page.locator('.recipient-picker').getByRole('combobox')
  await box.fill(name)
  const opt = page.getByRole('option', { name: new RegExp(name) })
  await expect(async () => {
    if (!(await opt.isVisible())) await box.press('ArrowDown')
    await expect(opt).toBeVisible({ timeout: 1_000 })
  }).toPass()
  await opt.click()
}

test.describe.serial('secrets composed in the UI', () => {
  let before: Record<string, unknown>
  test.beforeAll(async () => {
    before = await setSettings('/api/admin/settings/secrets', { enabled: true })
  })
  test.afterAll(async () => restoreSettings('/api/admin/settings/secrets', before))

  test('send a secret with a passphrase and a link; the recipient reveals it from Received', async ({
    page,
    browser,
  }) => {
    const sender = await newUser('employee', 'secretsender')
    const to = await newUser('client', 'secretto')
    await connect(sender, to)
    const label = `Router ${uid()}`
    await signIn(page, sender)
    await page.goto('/secrets')
    // The header's link; an empty box shows a second one once it has loaded.
    await page.getByRole('link', { name: 'New secret' }).first().click()
    await expect(page.getByRole('heading', { level: 1, name: 'Send a secret' })).toBeVisible()

    const send = page.getByTestId('secret-submit')
    await expect(send).toBeDisabled()
    await expect(page.getByTestId('submit-blockers')).toContainText('Enter the secret.')
    await page.getByTestId('secret-content-input').fill('hunter2-router')
    await page.getByRole('textbox', { name: /^Label/ }).fill(label)
    await pick(page, to.name)
    await page.getByTestId('passphrase').fill('a-long-passphrase')
    await page.getByTestId('passphrase-repeat').fill('a-different-one!')
    await expect(page.getByTestId('submit-blockers')).toContainText('The two passphrases differ.')
    await page.getByTestId('passphrase-repeat').fill('a-long-passphrase')
    await page.getByTestId('create-link').check()
    await expect(send).toBeEnabled()
    await send.click()
    await expect(page.getByTestId('secret-sent')).toBeVisible()
    const link = (await page.getByTestId('secret-link').innerText()).trim()
    expect(link).toMatch(/\/s#/)
    await expect(page.getByText('hunter2-router')).toHaveCount(0)

    const r = await session(browser, to)
    await r.goto('/secrets')
    await r.getByTestId('box-received').click()
    await r.getByText(label).click()
    await r.getByTestId('reveal-passphrase').fill('a-long-passphrase')
    await r.getByTestId('reveal-button').click()
    await expect(r.getByTestId('secret-content')).toHaveText('hunter2-router')
    await r.context().close()

    // The link reveals it too, for whoever holds it and the passphrase.
    const anon = await session(browser)
    await anon.goto(`/s#${link.split('#')[1]}`)
    await anon.getByTestId('reveal-passphrase').fill('a-long-passphrase')
    await anon.getByTestId('reveal-button').click()
    await expect(anon.getByTestId('secret-content')).toHaveText('hunter2-router')
    await anon.context().close()
  })

  test('the sender replaces and removes the link, burns the secret, and filters by state', async ({
    page,
    browser,
  }) => {
    const sender = await newUser('employee', 'secretmgr')
    // A recipient as well as the link: removing a secret's only way in ends it.
    const to = await newUser('client', 'secretmgrto')
    await connect(sender, to)
    const label = `Burn ${uid()}`
    await signIn(page, sender)
    await page.goto('/secrets/new')
    await page.getByTestId('secret-content-input').fill('to-be-burned')
    await page.getByRole('textbox', { name: /^Label/ }).fill(label)
    await pick(page, to.name)
    await page.getByTestId('create-link').check()
    await page.getByTestId('secret-submit').click()
    const first = (await page.getByTestId('secret-link').innerText()).trim()
    await page.getByRole('link', { name: 'See its status' }).click()
    await expect(page.getByRole('heading', { level: 1, name: label })).toBeVisible()

    await page.getByTestId('show-links').click()
    await page.getByRole('button', { name: 'Replace the link' }).click()
    await confirmDialog(page).click()
    const shown = page.locator('.link-list pre.link').first()
    await expect(shown).toHaveText(/\/s#/)
    await expect(shown).not.toHaveText(first)
    const anon = await session(browser)
    await anon.goto(`/s#${first.split('#')[1]}`)
    await expect(anon.getByTestId('public-secret-error')).toBeVisible()

    await page.getByRole('button', { name: 'Remove the link' }).click()
    await confirmDialog(page).click()
    await expect(page.getByRole('button', { name: 'Create a link' })).toBeVisible()
    await page.getByTestId('show-links').click()
    await expect(page.getByText('There are no links.')).toBeVisible()

    await page.getByTestId('burn-now').click()
    await confirmDialog(page).click()
    await expect(page.getByText('The secret was burned.')).toBeVisible()

    await page.goto('/secrets?box=sent')
    const row = page.locator('tbody tr', { hasText: label })
    await expect(row).toHaveCount(0)
    await page.getByRole('combobox', { name: 'State' }).selectOption({ label: 'Ended' })
    await expect(row).toHaveCount(1)
    await anon.context().close()
  })

  test('a secret request is composed with a link, then cancelled', async ({ page, browser }) => {
    const requester = await newUser('employee', 'requester')
    const target = await newUser('client', 'requested')
    await connect(requester, target)
    const label = `Asked ${uid()}`
    await signIn(page, requester)
    await page.goto('/secrets')
    await page.getByTestId('request-secret').click()
    await expect(page.getByRole('heading', { level: 1, name: 'Request a secret' })).toBeVisible()
    await page.getByTestId('request-label').fill(label)
    await page.getByTestId('request-note').fill('The staging DB password, please.')
    await pick(page, target.name)
    await page.getByTestId('request-create-link').check()
    await page.getByTestId('request-submit').click()
    await expect(page.getByTestId('request-sent')).toBeVisible()
    const link = (await page.getByTestId('request-link').innerText()).trim()
    expect(link).toMatch(/\/r#/)

    const t = await session(browser, target)
    await t.goto('/secrets')
    await t.getByTestId('box-requests').click()
    await t.getByTestId('requests-asked').click()
    await expect(t.getByText(label)).toBeVisible()

    await page.goto('/secrets')
    await page.getByTestId('box-requests').click()
    await page.getByTestId('requests-mine').click()
    await page.getByText(label).click()
    await expect(page.getByTestId('request-note-text')).toHaveText('The staging DB password, please.')
    await page.getByTestId('cancel-request').click()
    await confirmDialog(page).click()
    await expect(page.getByText('The request was cancelled.')).toBeVisible()
    await expect(page.getByTestId('cancel-request')).toHaveCount(0)
    // The person asked now gets the reason instead of the answer form.
    await t.goto(page.url())
    await expect(t.getByTestId('request-closed')).toHaveText('It was cancelled.')

    const anon = await session(browser)
    await anon.goto(`/r#${link.split('#')[1]}`)
    await expect(anon.getByTestId('public-request-error')).toBeVisible()
    for (const p of [t, anon]) await p.context().close()
  })

  test('a link secret set to burn is gone after the allowed wrong passphrases', async ({ browser }) => {
    // The allowance is read when the secret is sent: 2 here (10 by default).
    const KEY = 'secrets.passphrase_max_failures'
    const token = await admin()
    const knobs = await ok<{ items: Array<{ key: string; value: unknown; is_overridden: boolean }> }>(
      apiFetch(token, '/api/admin/settings/advanced'),
      'read knobs',
    )
    const was = knobs.items.find((i) => i.key === KEY)!
    const setKnob = (value: unknown) =>
      ok(
        apiFetch(token, '/api/admin/settings/advanced', {
          method: 'PUT',
          body: JSON.stringify({ updates: { [KEY]: value } }),
        }),
        'set knob',
      )
    await setKnob(2)
    let url: string
    try {
      const sent = await ok<{ link_url: string }>(
        apiFetch(token, '/api/secrets', {
          method: 'POST',
          body: JSON.stringify({
            content: `burned-${uid()}`,
            passphrase: 'the-right-passphrase',
            max_views: 1,
            create_link: true,
            burn_on_failures: true,
          }),
        }),
        'send secret',
      )
      url = sent.link_url
    } finally {
      await setKnob(was.is_overridden ? was.value : null)
    }

    const anon = await session(browser)
    await anon.goto(`/s#${url.split('#')[1]}`)
    await anon.getByTestId('reveal-passphrase').fill('wrong-one')
    await anon.getByTestId('reveal-button').click()
    await expect(anon.getByText('That passphrase is not correct.')).toBeVisible()
    await anon.getByTestId('reveal-passphrase').fill('wrong-two')
    await anon.getByTestId('reveal-button').click()
    await expect(anon.getByTestId('public-secret-error')).toContainText('This secret is no longer available.')
    await anon.reload()
    await expect(anon.getByTestId('public-secret-error')).toBeVisible()
    await anon.context().close()
  })
})
