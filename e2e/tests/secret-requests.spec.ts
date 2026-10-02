import { expect, test, type Browser, type Page } from '@playwright/test'

import { ADMIN, USER, apiFetch, apiLogin } from '../helpers'

/* Journey: secret requests (v2.24.0). The feature ships off, so the suite turns
 * it on as admin and off again afterwards. An account target answers a request
 * in the UI and the requester opens the answer; a link request sealed to the
 * requester's passphrase is answered anonymously through /r#<token>, cannot be
 * answered twice, and opens only with that passphrase - with the token kept out
 * of every API URL. */

async function signIn(browser: Browser, who: { email: string; password: string }): Promise<Page> {
  const ctx = await browser.newContext()
  const page = await ctx.newPage()
  await page.goto('/login')
  await page.fill('#login-email', who.email)
  await page.fill('#login-password', who.password)
  await page.click('button[type=submit]')
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
  return page
}

function openUntil(): string {
  return new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString()
}

test.describe.serial('secret requests', () => {
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

  test('an account target answers and the requester opens the answer', async ({ browser }) => {
    const search = await apiFetch(admin, `/api/users/search?q=${encodeURIComponent(USER.email)}`)
    const items = (await search.json()).items as Array<{ user_id: number; email: string }>
    const targetId = items.find((u) => u.email === USER.email)?.user_id
    expect(targetId).toBeTruthy()

    const created = await apiFetch(admin, '/api/secret-requests', {
      method: 'POST',
      body: JSON.stringify({
        label: 'E2E router password',
        note: 'The admin one, please.',
        expires_at: openUntil(),
        answer_max_views: 1,
        recipients: { user_ids: [targetId], group_ids: [], emails: [] },
      }),
    })
    expect(created.status).toBe(201)
    const rid = (await created.json()).id as string
    const content = `e2e-answer-${Date.now()}`

    const target = await signIn(browser, USER)
    await target.goto(`/secrets/requests/${rid}`)
    await expect(target.getByTestId('request-note-text')).toHaveText('The admin one, please.')
    await target.getByTestId('answer-content').fill(content)
    await target.getByTestId('answer-submit').click()
    await expect(target.getByTestId('answer-sent')).toBeVisible()
    await expect(target.getByText(content)).toHaveCount(0)

    const detail = await (await apiFetch(admin, `/api/secret-requests/${rid}`)).json()
    expect(detail.state).toBe('fulfilled')
    const requester = await signIn(browser, ADMIN)
    await requester.goto(`/secrets/${detail.answer_secret_id}`)
    await expect(requester.getByTestId('answer-note')).toBeVisible()
    await requester.getByTestId('reveal-button').click()
    await expect(requester.getByTestId('secret-content')).toHaveText(content)
  })

  test('a link request with the requester passphrase is answered through /r#token', async ({
    browser,
  }) => {
    const created = await apiFetch(admin, '/api/secret-requests', {
      method: 'POST',
      body: JSON.stringify({
        label: 'E2E Wi-Fi password',
        expires_at: openUntil(),
        answer_max_views: 1,
        passphrase: 'e2e-request-passphrase',
        create_link: true,
      }),
    })
    expect(created.status).toBe(201)
    const body = await created.json()
    const url = body.link_url as string
    expect(url).toContain('/r#')
    const token = url.split('#')[1]
    const content = `e2e-anon-answer-${Date.now()}`

    const anon = await browser.newContext()
    const page = await anon.newPage()
    const apiUrls: string[] = []
    page.on('request', (r) => {
      if (r.url().includes('/api/')) apiUrls.push(r.url())
    })
    await page.goto(`/r#${token}`)
    await expect(page.getByText('E2E Wi-Fi password')).toBeVisible()
    await page.getByTestId('answer-content').fill(content)
    await page.getByTestId('answer-submit').click()
    await expect(page.getByTestId('public-answer-sent')).toBeVisible()

    const again = await anon.newPage()
    again.on('request', (r) => {
      if (r.url().includes('/api/')) apiUrls.push(r.url())
    })
    await again.goto(`/r#${token}`)
    await expect(again.getByTestId('public-request-error')).toBeVisible()
    await anon.close()

    expect(apiUrls.length).toBeGreaterThan(0)
    expect(apiUrls.filter((u) => u.includes(token))).toEqual([])

    const detail = await (await apiFetch(admin, `/api/secret-requests/${body.id}`)).json()
    const requester = await signIn(browser, ADMIN)
    await requester.goto(`/secrets/${detail.answer_secret_id}`)
    await requester.getByTestId('reveal-request-passphrase').fill('wrong passphrase')
    await requester.getByTestId('reveal-button').click()
    await expect(requester.locator('.secret-reveal .fh-notice[data-tone=error]')).toBeVisible()
    await requester.getByTestId('reveal-request-passphrase').fill('e2e-request-passphrase')
    await requester.getByTestId('reveal-button').click()
    await expect(requester.getByTestId('secret-content')).toHaveText(content)
  })
})
