import { expect, test } from '@playwright/test'

import { ADMIN, USER, apiFetch, apiLogin } from '../helpers'

/* Journey: secrets (v2.24.0). The feature ships off, so the suite turns it on
 * as admin and off again afterwards. An account recipient reveals a one-view
 * secret in the UI and cannot reveal it again; the sender sees that it was
 * viewed, never what it said; an anonymous holder of a passphrase-protected
 * link reveals it through /s#<token>, with the token kept out of every API URL. */

test.describe.serial('secrets', () => {
  let admin = ''

  test.beforeAll(async () => {
    admin = await apiLogin(ADMIN.email, ADMIN.password)
    const r = await apiFetch(admin, '/api/admin/settings/secrets', {
      method: 'PUT',
      body: JSON.stringify({ enabled: true }),
    })
    expect(r.ok).toBeTruthy()
  })

  test.afterAll(async () => {
    await apiFetch(admin, '/api/admin/settings/secrets', {
      method: 'PUT',
      body: JSON.stringify({ enabled: false }),
    })
  })

  test('an account recipient reveals a one-view secret once', async ({ page, browser }) => {
    const search = await apiFetch(admin, `/api/users/search?q=${encodeURIComponent(USER.email)}`)
    const items = (await search.json()).items as Array<{ user_id: number; email: string }>
    const recipientId = items.find((u) => u.email === USER.email)?.user_id
    expect(recipientId).toBeTruthy()

    const content = `e2e-secret-${Date.now()}`
    const created = await apiFetch(admin, '/api/secrets', {
      method: 'POST',
      body: JSON.stringify({
        content,
        label: 'E2E VPN',
        max_views: 1,
        recipients: { user_ids: [recipientId], group_ids: [], emails: [] },
      }),
    })
    expect(created.status).toBe(201)
    const id = (await created.json()).id as string

    await page.goto('/login')
    await page.fill('#login-email', USER.email)
    await page.fill('#login-password', USER.password)
    await page.click('button[type=submit]')
    await expect(page).not.toHaveURL(/\/login(\?|$)/)

    await page.goto(`/secrets/${id}`)
    await expect(page.getByTestId('secret-reveal')).toBeVisible()
    await expect(page.getByTestId('last-view-warning')).toBeVisible()
    await expect(page.getByText(content)).toHaveCount(0) // nothing before Reveal
    await page.getByTestId('reveal-button').click()
    await expect(page.getByTestId('secret-content')).toHaveText(content)
    await expect(page.getByTestId('reveal-gone')).toBeVisible()

    await page.reload()
    await expect(page.getByTestId('not-revealable')).toBeVisible()
    await expect(page.getByText(content)).toHaveCount(0)

    // The sender (admin here) sees the view in the activity log - not the text.
    const ctx = await browser.newContext()
    const senderPage = await ctx.newPage()
    await senderPage.goto('/login')
    await senderPage.fill('#login-email', ADMIN.email)
    await senderPage.fill('#login-password', ADMIN.password)
    await senderPage.click('button[type=submit]')
    await expect(senderPage).not.toHaveURL(/\/login(\?|$)/)
    await senderPage.goto(`/secrets/${id}`)
    await expect(senderPage.locator('.activity table')).toContainText('Viewed')
    await expect(senderPage.getByText(content)).toHaveCount(0)
    await ctx.close()
  })

  test('a passphrase-protected link reveals through /s#token', async ({ browser }) => {
    const content = `e2e-link-secret-${Date.now()}`
    const created = await apiFetch(admin, '/api/secrets', {
      method: 'POST',
      body: JSON.stringify({
        content,
        passphrase: 'e2e-passphrase',
        max_views: 1,
        create_link: true,
      }),
    })
    expect(created.status).toBe(201)
    const url = (await created.json()).link_url as string
    expect(url).toContain('/s#')
    const token = url.split('#')[1]

    const anon = await browser.newContext()
    const pg = await anon.newPage()
    const apiUrls: string[] = []
    pg.on('request', (r) => {
      if (r.url().includes('/api/')) apiUrls.push(r.url())
    })
    await pg.goto(`/s#${token}`)
    await expect(pg.getByTestId('reveal-passphrase')).toBeVisible()
    await pg.getByTestId('reveal-passphrase').fill('wrong-passphrase')
    await pg.getByTestId('reveal-button').click()
    await expect(pg.locator('.secret-reveal .fh-notice[data-tone=error]')).toBeVisible()
    await pg.getByTestId('reveal-passphrase').fill('e2e-passphrase')
    await pg.getByTestId('reveal-button').click()
    await expect(pg.getByTestId('secret-content')).toHaveText(content)

    const again = await anon.newPage()
    await again.goto(`/s#${token}`)
    await expect(again.getByTestId('public-secret-error')).toBeVisible()
    await anon.close()

    expect(apiUrls.length).toBeGreaterThan(0)
    expect(apiUrls.filter((u) => u.includes(token))).toEqual([])
  })
})
