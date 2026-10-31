import { test, type BrowserContext } from '@playwright/test'

import { USER } from '../../helpers'
import {
  api,
  contextAs,
  expect,
  expectClean,
  ingestMail,
  newUser,
  uid,
  watch,
  type Person,
} from './_admin'

/* Breadth: every /admin route (frontend/src/router/index.ts, the children of
 * '/admin') as every role. An admin gets the page's own heading with no page
 * error and no 5xx; a client or an employee is bounced to their landing page;
 * a guest is sent to /login carrying the target. The API behind the console
 * answers 401 to a guest and 403 to a non-admin. COVERAGE.b.md maps each
 * route to these tests. */

type Ids = {
  user: number
  group: number
  mail: number
  inbox: number
  sso: string
}

/** [route name, path] - the path takes the ids seeded in beforeAll. */
const ROUTES: Array<[string, (ids: Ids) => string]> = [
  ['admin-overview', () => '/admin'],
  ['admin-users', () => '/admin/users'],
  ['admin-user-detail', (i) => `/admin/users/${i.user}`],
  ['admin-groups', () => '/admin/groups'],
  ['admin-group-detail', (i) => `/admin/groups/${i.group}`],
  ['admin-audit', () => '/admin/audit-log'],
  ['admin-analytics', () => '/admin/analytics'],
  ['admin-settings-webhooks', () => '/admin/settings/webhooks'],
  ['admin-mail-log', () => '/admin/mail-log'],
  ['admin-mail-detail', (i) => `/admin/mail-log/${i.mail}`],
  ['admin-error-log', () => '/admin/error-log'],
  ['admin-settings-error-alerts', () => '/admin/settings/error-alerts'],
  ['admin-inbox', () => '/admin/inbox'],
  ['admin-inbox-detail', (i) => `/admin/inbox/${i.inbox}`],
  ['admin-file-history', () => '/admin/file-history'],
  ['admin-sessions', () => '/admin/sessions'],
  ['admin-settings-sessions', () => '/admin/settings/sessions'],
  ['admin-quarantine', () => '/admin/quarantine'],
  ['admin-settings-quarantine', () => '/admin/settings/quarantine'],
  ['admin-api-tokens', () => '/admin/api-tokens'],
  ['admin-settings-api-tokens', () => '/admin/settings/api-tokens'],
  ['admin-settings-sso', () => '/admin/settings/sso'],
  ['admin-settings-sso-new', () => '/admin/settings/sso/new'],
  ['admin-settings-sso-edit', (i) => `/admin/settings/sso/${i.sso}`],
  ['admin-secrets', () => '/admin/secrets'],
  ['admin-secret-requests', () => '/admin/secret-requests'],
  ['admin-settings-secrets', () => '/admin/settings/secrets'],
  ['admin-settings-public-links', () => '/admin/settings/public-links'],
  ['admin-settings-transfers', () => '/admin/settings/transfers'],
  ['admin-settings-anomaly', () => '/admin/settings/anomaly'],
  ['admin-settings-encryption', () => '/admin/settings/encryption'],
  ['admin-settings-share-approval', () => '/admin/settings/share-approval'],
  ['admin-settings-email', () => '/admin/settings/email'],
  ['admin-settings-email-templates', () => '/admin/settings/email-templates'],
  ['admin-settings-imap', () => '/admin/settings/imap'],
  ['admin-settings-general', () => '/admin/settings/general'],
  ['admin-settings-branding', () => '/admin/settings/branding'],
  ['admin-settings-legal', () => '/admin/settings/legal'],
  ['admin-settings-twofa', () => '/admin/settings/twofa'],
  ['admin-settings-sign-in', () => '/admin/settings/sign-in'],
  ['admin-settings-email-change', () => '/admin/settings/email-change'],
  ['admin-ip-blocks', () => '/admin/ip-blocks'],
  ['admin-settings-scan-guard', () => '/admin/settings/scan-guard'],
  ['admin-settings-advanced', () => '/admin/settings/advanced'],
  ['admin-settings-backup', () => '/admin/settings/backup'],
  ['admin-settings-maintenance', () => '/admin/settings/maintenance'],
  ['admin-system', () => '/admin/system'],
  ['admin-scheduled-tasks', () => '/admin/scheduled-tasks'],
]

/** Every collection the console reads, no path parameters. */
const ADMIN_GETS = [
  '/api/admin/users',
  '/api/admin/invites',
  '/api/groups',
  '/api/admin/audit-log',
  '/api/admin/analytics',
  '/api/admin/webhooks',
  '/api/admin/mail-log',
  '/api/admin/error-log',
  '/api/admin/inbox',
  '/api/admin/files',
  '/api/admin/sessions',
  '/api/admin/api-tokens',
  '/api/admin/secrets',
  '/api/admin/secret-requests',
  '/api/admin/crons',
  '/api/admin/system/status',
  '/api/admin/scan-guard',
  '/api/admin/scan-guard/blocks',
  '/api/admin/settings/sso/providers',
  '/api/admin/settings/email',
  '/api/admin/settings/email-templates',
  '/api/admin/settings/imap',
  '/api/admin/settings/advanced',
  '/api/admin/settings/maintenance',
  '/api/admin/settings/encryption',
  '/api/admin/settings/twofa',
  '/api/admin/settings/site',
  '/api/admin/settings/branding',
  '/api/admin/settings/legal',
  '/api/admin/settings/secrets',
  '/api/admin/settings/share-approval',
  '/api/admin/settings/error-alerts',
  '/api/admin/settings/quarantine',
  '/api/admin/settings/email-change',
  '/api/admin/settings/api-tokens/policy',
]

let ids: Ids
let employee: Person

test.beforeAll(async () => {
  const s = uid()
  employee = await newUser('employee', 'sweep-emp')
  const group = await api<{ id: number }>('/api/groups', {
    method: 'POST',
    body: JSON.stringify({ name: `Sweep group ${s}`, description: '' }),
  })
  // A mail-log row of its own: an invitation to a unique address.
  const invitee = `sweep-${s}@e2e.local`
  await api('/api/account/invite', {
    method: 'POST',
    body: JSON.stringify({
      email: invitee,
      target_role: 'client',
      display_name_hint: 'Sweep',
    }),
  })
  let mails: { items: Array<{ id: number }> } = { items: [] }
  await expect
    .poll(async () => {
      mails = await api(`/api/admin/mail-log?recipient_email=${encodeURIComponent(invitee)}`)
      return mails.items.length
    })
    .toBe(1)
  const sso = await api<{ id: string }>('/api/admin/settings/sso/providers', {
    method: 'POST',
    body: JSON.stringify({
      name: `Sweep SSO ${s}`,
      preset: 'keycloak',
      issuer_url: 'https://sso.e2e.example/realms/sweep',
      client_id: 'fileheron',
      client_secret: 'e2e-not-a-secret',
      enabled: false,
    }),
  })
  ids = {
    user: employee.id,
    group: group.id,
    mail: mails.items[0].id,
    inbox: ingestMail(`Sweep mail ${s}`),
    sso: sso.id,
  }
})

test.describe('as the admin', () => {
  let ctx: BrowserContext
  test.beforeAll(async ({ browser }) => {
    ctx = await contextAs(browser)
  })
  test.afterAll(async () => ctx.close())

  for (const [name, path] of ROUTES) {
    test(`${name} renders its heading`, async () => {
      const page = await ctx.newPage()
      const w = watch(page)
      await page.goto(path(ids))
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      await expect(page.locator('.fh-notice[data-tone="error"]')).toHaveCount(0)
      // The sidebar marks the page's item (adminNav matchNames).
      await expect(
        page.locator('.admin-sidebar [aria-current="page"]').filter({ visible: true }),
      ).not.toHaveCount(0)
      await page.close()
      expectClean(w)
    })
  }

  test('the user menu leads to the admin console', async () => {
    const page = await ctx.newPage()
    await page.goto('/inbox')
    await page.getByRole('button', { name: 'Menu' }).click()
    await page.getByRole('menuitem', { name: 'Admin' }).click()
    await expect(page).toHaveURL(/\/admin$/)
    await page.close()
  })

  test('/admin/settings redirects to General', async () => {
    const page = await ctx.newPage()
    await page.goto('/admin/settings')
    await expect(page).toHaveURL(/\/admin\/settings\/general$/)
    await page.close()
  })

  test('/admin/settings/home-page lands on the home-page section of General', async () => {
    const page = await ctx.newPage()
    await page.goto('/admin/settings/home-page')
    await expect(page).toHaveURL(/\/admin\/settings\/general#home-page$/)
    await expect(page.locator('#home-page')).toBeVisible()
    await page.close()
  })

  test('an unknown admin path shows the not-found page', async () => {
    const page = await ctx.newPage()
    await page.goto('/admin/no-such-page')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await expect(page.getByText(/not found|404/i).first()).toBeVisible()
    await page.close()
  })

  test('detail pages for ids that do not exist show an error, not a crash', async () => {
    for (const path of [
      '/admin/users/99999999',
      '/admin/groups/99999999',
      '/admin/mail-log/99999999',
      '/admin/inbox/99999999',
    ]) {
      const page = await ctx.newPage()
      const w = watch(page)
      await page.goto(path)
      await expect(
        page.locator('[role="alert"], .fh-notice[data-tone="error"], .error').first(),
      ).toBeVisible()
      await page.close()
      expectClean(w)
    }
  })
})

for (const role of ['client', 'employee'] as const) {
  test.describe(`as a ${role}`, () => {
    let ctx: BrowserContext
    test.beforeAll(async ({ browser }) => {
      ctx = await contextAs(browser, role === 'client' ? USER : employee)
    })
    test.afterAll(async () => ctx.close())

    test(`every admin route bounces a ${role} to their landing page`, async () => {
      test.setTimeout(180_000) // ~50 page loads
      const page = await ctx.newPage()
      const w = watch(page)
      for (const [, path] of ROUTES) {
        await page.goto(path(ids))
        await expect(page, path(ids)).not.toHaveURL(/\/admin(\/|$)/)
        await expect(page.locator('.admin-sidebar')).toHaveCount(0)
      }
      await page.close()
      expectClean(w)
    })

    test(`the admin API answers 403 to a ${role}`, async () => {
      for (const p of ADMIN_GETS) {
        const r = await ctx.request.get(p, {
          headers: { authorization: `Bearer ${await accessToken(ctx)}` },
        })
        expect(r.status(), p).toBe(403)
      }
    })

    test(`the user menu offers no Admin item to a ${role}`, async () => {
      const page = await ctx.newPage()
      await page.goto('/inbox')
      await page.getByRole('button', { name: 'Menu' }).click()
      await expect(page.getByRole('menuitem', { name: 'Account' })).toBeVisible()
      await expect(page.getByRole('menuitem', { name: 'Admin' })).toHaveCount(0)
      await page.close()
    })
  })
}

test.describe('as a guest', () => {
  test('every admin route sends a guest to /login with the target kept', async ({ page }) => {
    test.setTimeout(180_000) // ~50 page loads
    const w = watch(page)
    for (const [, path] of ROUTES) {
      const target = path(ids)
      await page.goto(target)
      await expect
        .poll(
          () => {
            const u = new URL(page.url())
            return `${u.pathname} ${u.searchParams.get('redirect')}`
          },
          { message: target },
        )
        .toBe(`/login ${target}`)
    }
    expectClean(w)
  })

  test('the admin API answers 401 to a guest', async ({ request }) => {
    for (const p of ADMIN_GETS) {
      const r = await request.get(p)
      expect(r.status(), p).toBe(401)
    }
  })

  test('after signing in, the guest lands on the admin page they asked for', async ({ page }) => {
    await page.goto(`/admin/users/${ids.user}`)
    await page.locator('#login-email').fill('admin@e2e.local')
    await page.locator('#login-password').fill('AdminPass123!')
    await page.locator('button[type=submit]').click()
    await expect(page).toHaveURL(new RegExp(`/admin/users/${ids.user}$`))
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(employee.name)
  })
})

/** The context's access token, from a refresh on its own cookie. */
async function accessToken(ctx: BrowserContext): Promise<string> {
  const r = await ctx.request.post('/api/auth/refresh')
  expect(r.ok()).toBeTruthy()
  return (await r.json()).access_token as string
}
