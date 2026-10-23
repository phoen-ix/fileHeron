import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, truncateSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type BrowserContext, type Page } from '@playwright/test'

import { apiFetch, tokenFromStdout } from '../helpers'
import {
  ADMIN_EMAIL,
  ADMIN_PASSWORD,
  HELD_SUBJECT,
  HERO_SUBJECT,
  LINK_PASSWORD,
  PASSWORD,
  PEOPLE,
  REQUEST_LABEL,
  SECRET_LABEL,
  SSO_NAME,
  fileBytes,
  seedDemo,
  type Demo,
} from './seed'

/* The docs tour: one screenshot per screen (docs/screenshots/README.md is the
 * gallery), taken against the demo organisation in ./seed.ts. Run it through
 * scripts/docs-screenshots.sh, or with `-c playwright.docs.config.ts` against
 * a running stack.
 *
 * It is ALSO a render check (e2e.yml runs it after the specs): every test
 * asserts something page-specific before it shoots, and a page that throws
 * fails the test. Only `pageerror` counts - console errors do not, because a
 * signed-out SPA logs the expected 401 of its silent `/api/auth/refresh`.
 *
 * Each persona signs in ONCE, in beforeAll, and keeps its browser context: a
 * refresh token rotates on every page load, so a saved storage state would
 * replay a spent token and trip reuse detection. */

const OUT = path.resolve(
  process.env.DOCS_SCREENSHOT_DIR ?? fileURLToPath(new URL('../../docs/screenshots', import.meta.url)),
)
mkdirSync(OUT, { recursive: true })

const CREDS = {
  anna: [ADMIN_EMAIL, ADMIN_PASSWORD],
  lukas: [PEOPLE.lukas.email, PASSWORD],
  martin: [PEOPLE.martin.email, PASSWORD],
  julia: [PEOPLE.julia.email, PASSWORD],
} as const
type Who = keyof typeof CREDS

let demo: Demo
let now: Date
const ctxs = {} as Record<Who, BrowserContext>
let pageErrors: Error[] = []
const opened: Page[] = []

// Not serial: one broken page must not hide the others. A failure restarts
// the worker, which re-runs the (idempotent) seed.
test.beforeAll(async ({ browser }, info) => {
  test.setTimeout(300_000)
  const seedStart = new Date()
  demo = await seedDemo()
  const u = info.project.use
  for (const who of Object.keys(CREDS) as Who[]) {
    const ctx = await browser.newContext({
      baseURL: u.baseURL,
      viewport: u.viewport,
      deviceScaleFactor: u.deviceScaleFactor,
      locale: u.locale,
      timezoneId: u.timezoneId,
      userAgent: u.userAgent,
      ...u.contextOptions,
    })
    const page = await ctx.newPage()
    await signIn(page, ...CREDS[who])
    await page.close()
    ctxs[who] = ctx
  }
  now = backdate(seedStart)
})

test.afterAll(async () => {
  for (const ctx of Object.values(ctxs)) await ctx.close()
})

test.beforeEach(async ({ page }) => {
  pageErrors = []
  page.on('pageerror', (e) => pageErrors.push(e))
  await page.clock.setFixedTime(now)
})

test.afterEach(async () => {
  for (const p of opened.splice(0)) await p.close()
  expect(pageErrors.map((e) => e.message)).toEqual([])
})

/** A page in the persona's signed-in context, on the tour's fixed clock. */
async function as(who: Who): Promise<Page> {
  const page = await ctxs[who].newPage()
  opened.push(page)
  page.on('pageerror', (e) => pageErrors.push(e))
  await page.clock.setFixedTime(now)
  return page
}

/** Determinism: the server stamps rows with its own clock, so two runs would
 * differ in every timestamp shown. Move every DATETIME the stack wrote to the
 * run day's 06:00 UTC (rows from before the seed - the boot - to the day
 * before), keeping their order and spacing, and give the browser a clock just
 * after. Only with DOCS_DB_CONTAINER (scripts/docs-screenshots.sh); the
 * render check in e2e.yml keeps the live clock. */
function backdate(seedStart: Date): Date {
  const db = process.env.DOCS_DB_CONTAINER
  if (!db) return new Date()
  const anchor = new Date(seedStart)
  anchor.setUTCHours(6, 0, 0, 0)
  if (anchor > seedStart) anchor.setUTCDate(anchor.getUTCDate() - 1)
  const sqlTime = (d: Date) => d.toISOString().slice(0, 19).replace('T', ' ')
  const shift = Math.floor((seedStart.getTime() - anchor.getTime()) / 1000)
  const start = sqlTime(seedStart)
  const boot = sqlTime(new Date(anchor.getTime() - 86_400_000))
  const since = sqlTime(new Date(seedStart.getTime() - 3_600_000))
  // Generates one UPDATE per datetime column; the second client runs them.
  const col = "`', COLUMN_NAME, '`"
  const generate =
    `SELECT CONCAT('UPDATE \`', TABLE_NAME, '\` SET ${col} = IF(${col} < ''${start}'', ''${boot}'', ` +
    `${col} - INTERVAL ${shift} SECOND) WHERE ${col} >= ''${since}'';') FROM information_schema.COLUMNS ` +
    "WHERE TABLE_SCHEMA = DATABASE() AND DATA_TYPE IN ('datetime', 'timestamp') " +
    'ORDER BY TABLE_NAME, ORDINAL_POSITION;' +
    // Then spread the shares over the past days, oldest first like the seed,
    // so the lists read like a real outbox (and never sort a tie).
    Object.entries(SHARE_AGE_DAYS)
      .map(
        ([subject, days]) =>
          `SELECT 'UPDATE shares s LEFT JOIN files f ON f.share_id = s.id SET s.created_at = s.created_at - INTERVAL ${days} DAY, ` +
          `f.created_at = f.created_at - INTERVAL ${days} DAY WHERE s.subject = ''${subject}'';';`,
      )
      .join('')
  const client = 'mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"'
  // Once per stack: a worker restart re-runs beforeAll, and a second pass
  // would move the future timestamps (expiries) again. The boot rows, the
  // bootstrap admin among them, carry the marker.
  const done = `SELECT COUNT(*) FROM users WHERE created_at = '${boot}'`
  execFileSync(
    'docker',
    ['exec', '-i', db, 'sh', '-c', `[ "$(${client} -N -e "${done}")" != 0 ] || ${client} -N | ${client}`],
    { input: generate },
  )
  return new Date(anchor.getTime() + 5 * 60_000)
}

const MB = 1024 * 1024

const SHARE_AGE_DAYS: Record<string, number> = {
  'Kick-off meeting minutes': 11,
  'Service agreement - draft v2': 8,
  'Tender package': 5,
  'Q3 status report': 2,
  'Signed building permit': 1,
}

/** A file of the given size on disk, sparse (only its magic bytes are real),
 * for the upload form: Playwright caps in-memory buffers at 50 MB in total. */
function sparseFile(f: { name: string; bytes: number; type: string }): string {
  const p = path.join(mkdtempSync(path.join(tmpdir(), 'fh-docs-')), f.name)
  writeFileSync(p, fileBytes({ ...f, bytes: 16 }))
  truncateSync(p, Math.round(f.bytes))
  return p
}

async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/login')
  await page.fill('#login-email', email)
  await page.fill('#login-password', password)
  await page.click('button[type=submit]')
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
}

const ADMIN_HEIGHT = 640
const ADMIN_CROP = { within: '.admin-content' }

const title = (page: Page, t: string) => expect(page).toHaveTitle(`${t} · file:Heron`)

/** `clipAbove`: the whole page from the top down to just above that element -
 * for a page too tall to show whole. `fullPage` stops at 2400 CSS px.
 * `within`: only that element, at most `ADMIN_HEIGHT` tall - the admin pages
 * without the sidebar every admin shot would repeat (the size budget). */
async function shoot(
  page: Page,
  name: string,
  opts: { fullPage?: boolean; clipAbove?: string; within?: string } = {},
): Promise<void> {
  await page.evaluate(() => document.fonts.ready)
  // The pages rise into place (`fh-rise`), and a box measured mid-flight
  // moves the crop between runs: let every finite animation end first.
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => Number.isFinite(Number(a.effect?.getComputedTiming().endTime)))
        .map((a) => a.finished.catch(() => undefined)),
    ),
  )
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 15_000 })
  await expect(page.getByText('Loading…', { exact: true })).toHaveCount(0)
  // From the top, with the pointer parked where it hovers nothing.
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.mouse.move(0, 0)
  const width = page.viewportSize()!.width
  let clip: { x: number; y: number; width: number; height: number } | undefined
  if (opts.clipAbove) {
    const box = await page.locator(opts.clipAbove).boundingBox()
    if (!box) throw new Error(`[docs] ${opts.clipAbove} is not on the page`)
    clip = { x: 0, y: 0, width, height: Math.min(2400, Math.floor(box.y) - 16) }
  } else if (opts.within) {
    const box = await page.locator(opts.within).boundingBox()
    if (!box) throw new Error(`[docs] ${opts.within} is not on the page`)
    clip = { x: box.x, y: box.y, width: box.width, height: Math.min(ADMIN_HEIGHT, box.height) }
  } else if (opts.fullPage) {
    const height = await page.evaluate(() => document.documentElement.scrollHeight)
    clip = { x: 0, y: 0, width, height: Math.min(2400, height) }
  }
  await page.screenshot({
    path: path.join(OUT, `${name}.png`),
    animations: 'disabled',
    caret: 'hide',
    fullPage: Boolean(clip),
    clip,
  })
}

/* --- first: the logs, before the tour's own sign-ins and unlocks add rows
 * at the server's real time ------------------------------------------------ */

test('admin audit log', async () => {
  const page = await as('anna')
  await page.goto('/admin/audit-log')
  await title(page, 'Audit log')
  await expect(page.locator('h1.admin-page-title')).toBeVisible()
  await shoot(page, 'admin-audit-log', ADMIN_CROP)
})

test('admin mail log + one mail', async () => {
  const page = await as('anna')
  await page.goto('/admin/mail-log')
  await title(page, 'Mail log')
  // A mail whose body carries no send-time timestamp.
  const first = page.locator('tr', { hasText: 'needs your approval' }).locator('a[href^="/admin/mail-log/"]').first()
  await expect(first).toBeVisible()
  await shoot(page, 'admin-mail-log', ADMIN_CROP)
  await first.click()
  await title(page, 'Mail')
  await shoot(page, 'admin-mail-detail', ADMIN_CROP)
})

/* --- signed out --------------------------------------------------------- */

test('login', async ({ page }) => {
  await page.goto('/login')
  await expect(page.locator('#login-email')).toBeVisible()
  await shoot(page, 'login')
})

test('login: second factor', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#login-email', PEOPLE.thomas.email)
  await page.fill('#login-password', PASSWORD)
  await page.click('button[type=submit]')
  // The password step reveals one code field on the same page.
  await expect(page.getByLabel('Authentication code')).toBeVisible()
  await shoot(page, 'login-2fa')
})

test('first-admin setup', async ({ page }) => {
  // Only a stack without an admin shows it; answer the status check as one.
  await page.route('**/api/setup/status', (r) => r.fulfill({ json: { required: true, token_required: false } }))
  await page.goto('/setup')
  await title(page, 'Set up fileHeron')
  await expect(page.locator('.setup-title')).toBeVisible()
  await shoot(page, 'setup')
})

for (const [route, name, heading] of [
  ['/register/demo-invite-token', 'register', 'Set up your account'],
  ['/forgot-password', 'forgot-password', 'Forgot password'],
  ['/reset-password/demo-reset-token', 'reset-password', 'Reset password'],
  ['/verify-email/expired-demo-token', 'verify-email-invalid', 'Verify email'],
] as const) {
  test(`auth page ${name}`, async ({ page }) => {
    await page.goto(route)
    await title(page, heading)
    await expect(page.locator('h1')).toBeVisible()
    if (name === 'verify-email-invalid') await expect(page.locator('[role=alert], .fh-notice').first()).toBeVisible()
    await shoot(page, name)
  })
}

test('public link: locked', async ({ page }) => {
  await page.goto(`/d/${demo.heroLinkToken}`)
  await expect(page.locator('form.unlock-form input[type=password]')).toBeVisible()
  await shoot(page, 'public-link-locked')
})

test('public link page', async ({ page }) => {
  await page.goto(`/d/${demo.heroLinkToken}`)
  await page.locator('input[type=password]').fill(LINK_PASSWORD)
  await page.locator('form.unlock-form button').click()
  await expect(page.getByText('A-101 Ground floor plan rev C.pdf')).toBeVisible()
  await shoot(page, 'public-link')
})

test('public secret link', async ({ page }) => {
  await page.goto(`/s#${demo.secretLinkToken}`)
  await title(page, 'Secret')
  await expect(page.getByText(SECRET_LABEL)).toBeVisible()
  await shoot(page, 'public-secret')
})

test('public secret request link', async ({ page }) => {
  await page.goto(`/r#${demo.requestLinkToken}`)
  await title(page, 'Secret request')
  await expect(page.getByRole('heading', { name: REQUEST_LABEL })).toBeVisible()
  await shoot(page, 'public-secret-request')
})

test('manage notifications', async ({ page }) => {
  // The link rides every notification mail; with SMTP unset the worker
  // prints the mail, so the newest one comes from its log.
  const worker = (process.env.E2E_BACKEND_CONTAINER ?? 'fileheron_e2e-backend').replace(/-backend$/, '-worker')
  await page.goto(`/manage-notifications/${tokenFromStdout('manage-notifications', worker)}`)
  await title(page, 'Manage subscriptions')
  await expect(page.locator('h1')).toBeVisible()
  await expect(page.locator('.error-state')).toHaveCount(0)
  await shoot(page, 'manage-notifications')
})

test('imprint', async ({ page }) => {
  await page.goto('/imprint')
  await expect(page.locator('.legal-content')).toContainText('Heron Demo Architects')
  await shoot(page, 'legal-imprint')
})

test('not found', async ({ page }) => {
  await page.goto('/no-such-page')
  await title(page, 'Not found')
  await expect(page.locator('h1')).toBeVisible()
  await shoot(page, 'not-found')
})

/* --- employees and clients ---------------------------------------------- */

test('home', async () => {
  const page = await as('lukas')
  await page.goto('/')
  await expect(page.locator('.welcome-card')).toContainText(PEOPLE.lukas.displayName)
  await shoot(page, 'home')
})

test('share page (hero)', async () => {
  const page = await as('lukas')
  await page.goto(`/share/${demo.heroShareId}`)
  await expect(page.getByRole('heading', { name: HERO_SUBJECT })).toBeVisible()
  await expect(page.getByText('A-101 Ground floor plan rev C.pdf')).toBeVisible()
  await expect(page.locator('.public-link-panel .qr-svg svg')).toBeVisible()
  // Down to the public URL; the QR code below it would double the height.
  await shoot(page, 'share-page', { clipAbove: '.public-link-panel .qr-section' })
})

test('share page: end-share dialog', async () => {
  const page = await as('lukas')
  await page.goto(`/share/${demo.heroShareId}`)
  await page.getByRole('button', { name: 'End share' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await shoot(page, 'share-page-end-dialog')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('new share', async () => {
  const page = await as('lukas')
  await page.goto('/share/new')
  await page.locator('input.dropzone-input').setInputFiles(
    [
      { name: 'Riverside site photos.zip', bytes: 148.2 * MB, type: 'application/zip' },
      { name: 'Survey report.pdf', bytes: 3.4 * MB, type: 'application/pdf' },
      { name: 'Levels.dwg', bytes: 6.1 * MB, type: 'application/octet-stream' },
    ].map(sparseFile),
  )
  await page.getByLabel('Subject', { exact: true }).fill('Site survey and photos')
  await page
    .getByLabel('Message', { exact: true })
    .fill('Martin, the survey from Tuesday plus the drone photos. Levels are in the DWG.')
  const picker = page.locator('.recipient-picker input[role=combobox]')
  await picker.fill('Riverside')
  await page.locator('.result-row', { hasText: 'Project Riverside' }).click()
  // The list closes on a pick and opens on focus, which fill() on the
  // still-focused input never fires. Blur first - and outwait the picker's
  // 120 ms delayed blur handler, or it shuts the list fill() just opened.
  await picker.blur()
  await page.waitForTimeout(300)
  await picker.fill('Martin')
  await page.locator('.result-row', { hasText: 'Martin Keller' }).click()
  await expect(page.locator('.recipient-picker .chip')).toHaveCount(2)
  await shoot(page, 'share-new', { fullPage: true })
})

test('outbox', async () => {
  const page = await as('lukas')
  await page.goto('/outbox')
  // Every state, not the default "active only", so the pills show.
  await page.locator('select', { has: page.locator('option[value="revoked"]') }).selectOption('')
  await expect(page.getByText(HERO_SUBJECT)).toBeVisible()
  await expect(page.getByText('Service agreement - draft v2')).toBeVisible()
  await shoot(page, 'outbox')
})

test('outbox on a phone', async () => {
  const page = await as('lukas')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/outbox')
  await expect(page.getByText(HERO_SUBJECT)).toBeVisible()
  await shoot(page, 'outbox.mobile')
})

test('outbox: empty', async () => {
  const page = await as('julia')
  await page.goto('/outbox')
  await title(page, 'Sent')
  await expect(page.locator('.empty-state')).toBeVisible()
  await shoot(page, 'outbox-empty')
})

test('inbox (client)', async () => {
  const page = await as('martin')
  await page.goto('/inbox')
  await expect(page.getByText(HERO_SUBJECT)).toBeVisible()
  await shoot(page, 'inbox')
})

test('approvals', async () => {
  const page = await as('anna')
  await page.goto('/approvals')
  await expect(page.getByText(HELD_SUBJECT)).toBeVisible()
  await shoot(page, 'approvals')
})

test('account', async () => {
  const page = await as('lukas')
  await page.goto('/account')
  await expect(page.getByText(PEOPLE.lukas.email).first()).toBeVisible()
  await shoot(page, 'account')
})

test('account: two-factor setup', async () => {
  const page = await as('lukas')
  await page.goto('/account/2fa')
  await title(page, 'Two-factor authentication')
  await expect(page.locator('h1')).toBeVisible()
  await shoot(page, 'account-2fa')
})

test('secrets', async () => {
  const page = await as('lukas')
  await page.goto('/secrets?box=sent')
  await expect(page.getByText(SECRET_LABEL)).toBeVisible()
  await shoot(page, 'secrets')
})

test('secret detail', async () => {
  const page = await as('lukas')
  await page.goto(`/secrets/${demo.secretId}`)
  await expect(page.getByRole('heading', { name: SECRET_LABEL })).toBeVisible()
  await shoot(page, 'secrets-detail')
})

test('new secret', async () => {
  const page = await as('lukas')
  await page.goto('/secrets/new')
  await title(page, 'New secret')
  await expect(page.locator('h1')).toBeVisible()
  await shoot(page, 'secrets-new')
})

test('secret request detail', async () => {
  const page = await as('lukas')
  await page.goto(`/secrets/requests/${demo.requestId}`)
  await expect(page.getByRole('heading', { name: REQUEST_LABEL })).toBeVisible()
  await shoot(page, 'secrets-request-detail')
})

test('new secret request', async () => {
  const page = await as('lukas')
  await page.goto('/secrets/requests/new')
  await title(page, 'Request a secret')
  await expect(page.locator('h1')).toBeVisible()
  await shoot(page, 'secrets-request-new')
})

test('share page in German', async () => {
  const setLocale = (locale: 'de' | 'en') =>
    apiFetch(demo.lukasToken, '/api/account/locale', { method: 'PATCH', body: JSON.stringify({ locale }) })
  expect((await setLocale('de')).ok).toBeTruthy()
  try {
    const page = await as('lukas')
    await page.goto(`/share/${demo.heroShareId}`)
    await expect(page.getByRole('heading', { name: HERO_SUBJECT })).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('lang', 'de')
    await expect(page.locator('.public-link-panel .qr-svg svg')).toBeVisible()
    await shoot(page, 'share-page.de', { clipAbove: '.public-link-panel .qr-section' })
  } finally {
    await setLocale('en')
  }
})

/* --- admin console: one shot per page ----------------------------------- */

test('admin overview', async () => {
  const page = await as('anna')
  await page.goto('/admin')
  // All six sidebar categories: v2.17.1 shipped a sidebar showing only
  // "Overview", and nothing that ran before a release rendered it.
  await expect(page.locator('.nav-cat')).toHaveCount(6)
  // The attention tiles and the status line show "…" until they load.
  await expect(page.getByText('…', { exact: true })).toHaveCount(0)
  await shoot(page, 'admin-overview')
})

test('admin users', async () => {
  const page = await as('anna')
  await page.goto('/admin/users')
  await expect(page.getByText(PEOPLE.julia.email)).toBeVisible()
  await shoot(page, 'admin-users')
})

test('admin user detail', async () => {
  const page = await as('anna')
  await page.goto(`/admin/users/${demo.users.lukas}`)
  await title(page, 'User')
  await expect(page.getByText(PEOPLE.lukas.email).first()).toBeVisible()
  await shoot(page, 'admin-user-detail', ADMIN_CROP)
})

test('admin group detail', async () => {
  const page = await as('anna')
  await page.goto(`/admin/groups/${demo.groupId}`)
  await title(page, 'Group')
  await expect(page.getByText(PEOPLE.martin.displayName).first()).toBeVisible()
  await shoot(page, 'admin-group-detail', ADMIN_CROP)
})

test('admin SSO provider', async () => {
  const page = await as('anna')
  await page.goto(`/admin/settings/sso/${demo.ssoId}`)
  await title(page, 'Edit SSO provider')
  await expect(page.locator('input').first()).toBeVisible()
  await shoot(page, 'admin-sso-edit', ADMIN_CROP)
})

// [path, screenshot, document title]. Pages under a tab shell carry the tab
// in their title. RENDER_ONLY: settings forms built from the same sections
// and switches as the shot ones - visited and checked, not photographed, so the
// set stays inside its size budget.
const ADMIN_PAGES = [
  ['/admin/groups', 'admin-groups', 'Groups'],
  ['/admin/file-history', 'admin-file-history', 'File history'],
  ['/admin/analytics', 'admin-analytics', 'Analytics'],
  ['/admin/sessions', 'admin-sessions', 'Sessions'],
  ['/admin/api-tokens', 'admin-api-tokens', 'API tokens'],
  ['/admin/secrets', 'admin-secrets', 'Secrets'],
  ['/admin/secret-requests', 'admin-secret-requests', 'Secret requests'],
  ['/admin/quarantine', 'admin-quarantine', 'Quarantine'],
  ['/admin/error-log', 'admin-error-log', 'Errors & alerts · Log'],
  ['/admin/ip-blocks', 'admin-ip-blocks', 'Blocked sources'],
  ['/admin/inbox', 'admin-inbox', 'Inbox'],
  ['/admin/settings/webhooks', 'admin-webhooks', 'Webhooks'],
  ['/admin/settings/general', 'admin-general', 'General'],
  ['/admin/settings/branding', 'admin-branding', 'Branding & legal'],
  ['/admin/settings/public-links', 'admin-public-links', 'Public links'],
  ['/admin/settings/share-approval', 'admin-share-approval', 'Share approval'],
  ['/admin/settings/encryption', 'admin-encryption', 'Encryption at rest'],
  ['/admin/settings/sign-in', 'admin-sign-in', 'Sign-in policies'],
  ['/admin/settings/twofa', 'admin-twofa', '2FA enforcement'],
  ['/admin/settings/sso', 'admin-sso', 'SSO providers'],
  ['/admin/settings/email', 'admin-email', 'Outgoing mail (SMTP)'],
  ['/admin/settings/email-templates', 'admin-email-templates', 'Email templates'],
  ['/admin/settings/advanced', 'admin-advanced', 'Data retention & storage'],
  ['/admin/settings/backup', 'admin-backup', 'Backup & restore'],
  ['/admin/settings/maintenance', 'admin-maintenance', 'Maintenance mode'],
  ['/admin/scheduled-tasks', 'admin-scheduled-tasks', 'Scheduled tasks'],
  ['/admin/system', 'admin-system', 'Status & updates'],
] as const

const RENDER_ONLY = [
  ['/admin/settings/sessions', 'Sessions · Policy'],
  ['/admin/settings/api-tokens', 'API tokens · Policy'],
  ['/admin/settings/secrets', 'Secrets · Policy'],
  ['/admin/settings/quarantine', 'Quarantine · Alerts & scanner'],
  ['/admin/settings/error-alerts', 'Errors & alerts · Alerts'],
  ['/admin/settings/scan-guard', 'Blocked sources · Auto-block rules'],
  ['/admin/settings/legal', 'Legal pages'],
  ['/admin/settings/transfers', 'Files & transfers'],
  ['/admin/settings/email-change', 'Sign-in policies · Email change'],
  ['/admin/settings/imap', 'Inbound mail (IMAP)'],
  ['/admin/settings/anomaly', 'Anomaly detection'],
  ['/admin/settings/sso/new', 'New SSO provider'],
] as const

for (const [route, name, heading] of ADMIN_PAGES) {
  test(`admin page ${name}`, async () => {
    const page = await as('anna')
    await page.goto(route)
    await title(page, heading)
    await expect(page.locator('h1.admin-page-title')).toBeVisible()
    if (name === 'admin-sso') await expect(page.getByText(SSO_NAME)).toBeVisible()
    await shoot(page, name, ADMIN_CROP)
  })
}

for (const [route, heading] of RENDER_ONLY) {
  test(`admin page ${route} (render check)`, async () => {
    const page = await as('anna')
    await page.goto(route)
    await title(page, heading)
    await expect(page.locator('h1.admin-page-title')).toBeVisible()
    await expect(page.getByText('Loading…', { exact: true })).toHaveCount(0)
  })
}
