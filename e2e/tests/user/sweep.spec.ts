import type { Browser, Page } from '@playwright/test'

import {
  createShare,
  expect,
  newUser,
  ok,
  restoreSettings,
  setSettings,
  signIn,
  test,
  tokenOf,
  watch,
  type Persona,
} from './lib'
import { apiFetch } from '../../helpers'

/* Breadth: every route outside /admin, as every role. Each visit lands where
 * the router sends that role, shows a level-1 heading and a titled tab, and
 * throws no page error and gets no 5xx (the `guard` fixture). The depth - what
 * each page DOES - is in the journey specs next to this one. */

type Who = 'guest' | 'client' | 'employee' | 'admin'
const WHO: Who[] = ['guest', 'client', 'employee', 'admin']

interface Data {
  shareId: string
  linkToken: string
  secretId: string
  secretLink: string
  requestId: string
  requestLink: string
}

/** [route, url, public?, where a signed-in user ends up (default: the url)]. */
const ROUTES: Array<[string, (d: Data) => string, boolean, RegExp?]> = [
  ['/setup', () => '/setup', true, /\/(share\/new|outbox|inbox|account)?$/],
  ['/login', () => '/login', true, /\/(share\/new|outbox|inbox|account)?$/],
  ['/login/2fa', () => '/login/2fa', true, /\/(share\/new|outbox|inbox|account)?$/],
  ['/register/:token', () => '/register/not-a-real-invite', true],
  ['/forgot-password', () => '/forgot-password', true],
  ['/reset-password/:token', () => '/reset-password/not-a-real-token', true],
  ['/verify-email/:token', () => '/verify-email/not-a-real-token', true],
  ['/confirm-email-change/:token', () => '/confirm-email-change/not-a-real-token', true],
  ['/cancel-email-change/:token', () => '/cancel-email-change/not-a-real-token', true],
  ['/', () => '/', false, /\/(share\/new|outbox|inbox|account)?$/],
  ['/account', () => '/account', false],
  ['/account/2fa', () => '/account/2fa', false],
  ['/account/2fa/forced', () => '/account/2fa/forced', false, /\/account\/2fa$/],
  ['/outbox', () => '/outbox', false],
  ['/inbox', () => '/inbox', false],
  ['/share/new', () => '/share/new', false],
  ['/approvals', () => '/approvals', false],
  ['/share/:id', (d) => `/share/${d.shareId}`, false],
  ['/secrets', () => '/secrets', false],
  ['/secrets/new', () => '/secrets/new', false],
  ['/secrets/requests/new', () => '/secrets/requests/new', false],
  ['/secrets/requests/:id', (d) => `/secrets/requests/${d.requestId}`, false],
  ['/secrets/:id', (d) => `/secrets/${d.secretId}`, false],
  ['/d/:token', (d) => `/d/${d.linkToken}`, true],
  ['/s', (d) => `/s#${d.secretLink}`, true],
  ['/r', (d) => `/r#${d.requestLink}`, true],
  ['/manage-notifications/:token', () => '/manage-notifications/not-a-real-token', true],
  ['/imprint', () => '/imprint', true],
  ['/privacy', () => '/privacy', true],
  ['/:pathMatch(.*)*', () => '/no-such-page', true],
]

let data: Data
const people = {} as Record<Exclude<Who, 'guest'>, Persona>
const pages = {} as Record<Who, Page>
let secretsBefore: Record<string, unknown>

test.beforeAll(async ({ browser }) => {
  test.setTimeout(120_000)
  for (const r of ['client', 'employee', 'admin'] as const) people[r] = await newUser(r, `sweep-${r}`)
  secretsBefore = await setSettings('/api/admin/settings/secrets', { enabled: true })

  const owner = await tokenOf(people.admin)
  const recipients = [people.client.id, people.employee.id]
  const share = await createShare(owner, {
    users: recipients,
    files: { 'sweep.txt': 'sweep' },
    publicLink: { password: null, download_limit: null },
  })
  const secret = await ok<{ id: string; link_url: string }>(
    apiFetch(owner, '/api/secrets', {
      method: 'POST',
      body: JSON.stringify({
        content: 'sweep secret',
        label: 'Sweep secret',
        max_views: 5,
        create_link: true,
        recipients: { user_ids: recipients, group_ids: [], emails: [] },
      }),
    }),
    'create secret',
  )
  const request = await ok<{ id: string; link_url: string }>(
    apiFetch(owner, '/api/secret-requests', {
      method: 'POST',
      body: JSON.stringify({
        label: 'Sweep request',
        expires_at: new Date(Date.now() + 86_400_000).toISOString(),
        answer_max_views: 1,
        create_link: true,
        recipients: { user_ids: recipients, group_ids: [], emails: [] },
      }),
    }),
    'create secret request',
  )
  data = {
    shareId: share.id,
    linkToken: share.linkUrl!.split('/d/')[1],
    secretId: secret.id,
    secretLink: secret.link_url.split('#')[1],
    requestId: request.id,
    requestLink: request.link_url.split('#')[1],
  }
  for (const who of WHO) pages[who] = await open(browser, who === 'guest' ? undefined : people[who])
})

test.afterAll(async () => {
  for (const p of Object.values(pages)) await p.context().close()
  await restoreSettings('/api/admin/settings/secrets', secretsBefore)
})

/** One context per role, signed in once: a refresh token rotates on every
 * page load, so contexts are kept rather than re-created from saved state. */
async function open(browser: Browser, p?: Persona): Promise<Page> {
  const page = watch(await (await browser.newContext()).newPage())
  if (p) await signIn(page, p)
  return page
}

for (const who of WHO) {
  test.describe(who, () => {
    for (const [route, url, isPublic, landing] of ROUTES) {
      test(route, async () => {
        const page = pages[who]
        const target = url(data)
        await page.goto(target)
        if (who === 'guest' && !isPublic) {
          await expect(page).toHaveURL(/\/login(\?redirect=|$)/)
        } else if (who === 'guest' && ['/setup', '/login/2fa'].includes(route)) {
          // Setup is done (an admin exists); no pending second factor.
          await expect(page).toHaveURL(/\/login$/)
        } else if (who !== 'guest' && landing) {
          await expect(page).toHaveURL(landing)
        } else {
          // Stays put (a link's #token stays in the fragment).
          await expect(page).toHaveURL((u) => u.pathname === target.split('#')[0])
        }
        await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible()
        await expect(page).toHaveTitle(/file:Heron$/)
        await expect(page.getByText('Loading…', { exact: true })).toHaveCount(0)
      })
    }

    if (who === 'client' || who === 'employee') {
      test('/admin is bounced to the landing page', async () => {
        const page = pages[who]
        await page.goto('/admin')
        await expect(page).not.toHaveURL(/\/admin/)
        await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible()
      })
    }
  })
}

test('a deep link survives the sign-in', async ({ page }) => {
  const p = await newUser('client', 'deeplink')
  await page.goto('/account')
  await expect(page).toHaveURL(/\/login\?redirect=(%2F|\/)account$/)
  await page.getByLabel('Email', { exact: true }).fill(p.email)
  await page.getByLabel('Password', { exact: true }).fill(p.password)
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page).toHaveURL(/\/account$/)
  await expect(page.getByRole('heading', { level: 1, name: p.name })).toBeVisible()
})
