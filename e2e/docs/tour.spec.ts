import { mkdirSync, mkdtempSync, truncateSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'

import { apiFetch, apiLogin } from '../helpers'
import {
  ADMIN_EMAIL,
  ADMIN_PASSWORD,
  HELD_SUBJECT,
  HERO_SUBJECT,
  LINK_PASSWORD,
  PASSWORD,
  PEOPLE,
  fileBytes,
  seedDemo,
  type Demo,
} from './seed'

/* The docs tour: one screenshot per page the README shows, taken against the
 * demo organisation in ./seed.ts. Run with `-c playwright.docs.config.ts`.
 *
 * It is ALSO a render check (e2e.yml runs it after the specs): every test
 * asserts something page-specific before it shoots, and a page that throws
 * fails the test. Only `pageerror` counts - console errors do not, because a
 * signed-out SPA logs the expected 401 of its silent `/api/auth/refresh`. */

const OUT = path.resolve(
  process.env.DOCS_SCREENSHOT_DIR ?? fileURLToPath(new URL('../../docs/screenshots', import.meta.url)),
)
mkdirSync(OUT, { recursive: true })

let demo: Demo
let pageErrors: Error[] = []

// Not serial: one broken page must not hide the others. A failure restarts
// the worker, which re-runs the (idempotent) seed.
test.beforeAll(async () => {
  demo = await seedDemo()
})

test.beforeEach(async ({ page }) => {
  pageErrors = []
  page.on('pageerror', (e) => pageErrors.push(e))
})

test.afterEach(() => {
  expect(pageErrors.map((e) => e.message)).toEqual([])
})

const MB = 1024 * 1024

/** A file of the given size on disk, sparse (only its magic bytes are real),
 * for the upload form: Playwright caps in-memory buffers at 50 MB in total. */
function sparseFile(f: { name: string; bytes: number; type: string }): string {
  const p = path.join(mkdtempSync(path.join(tmpdir(), 'fh-docs-')), f.name)
  writeFileSync(p, fileBytes({ ...f, bytes: 16 }))
  truncateSync(p, Math.round(f.bytes))
  return p
}

async function signIn(page: Page, email: string, password = PASSWORD): Promise<void> {
  await page.goto('/login')
  await page.fill('#login-email', email)
  await page.fill('#login-password', password)
  await page.click('button[type=submit]')
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
}

/** `clipAbove`: the whole page from the top down to just above that element -
 * for a page too tall to show whole. */
async function shoot(
  page: Page,
  name: string,
  opts: { fullPage?: boolean; clipAbove?: string } = {},
): Promise<void> {
  await page.evaluate(() => document.fonts.ready)
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 15_000 })
  await expect(page.getByText('Loading…', { exact: true })).toHaveCount(0)
  // From the top, with the pointer parked where it hovers nothing.
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.mouse.move(0, 0)
  let clip: { x: number; y: number; width: number; height: number } | undefined
  if (opts.clipAbove) {
    const box = await page.locator(opts.clipAbove).boundingBox()
    if (!box) throw new Error(`[docs] ${opts.clipAbove} is not on the page`)
    clip = { x: 0, y: 0, width: page.viewportSize()!.width, height: Math.floor(box.y) - 16 }
  }
  await page.screenshot({
    path: path.join(OUT, `${name}.png`),
    animations: 'disabled',
    caret: 'hide',
    fullPage: Boolean(opts.fullPage || clip),
    clip,
  })
}

test('login', async ({ page }) => {
  await page.goto('/login')
  await expect(page.locator('#login-email')).toBeVisible()
  await shoot(page, 'login')
})

test('share page (hero)', async ({ page }) => {
  await signIn(page, PEOPLE.lukas.email)
  await page.goto(`/share/${demo.heroShareId}`)
  await expect(page.getByRole('heading', { name: HERO_SUBJECT })).toBeVisible()
  await expect(page.getByText('A-101 Ground floor plan rev C.pdf')).toBeVisible()
  await expect(page.locator('.public-link-panel .qr-svg svg')).toBeVisible()
  // Down to the public URL; the QR code below it would double the height.
  await shoot(page, 'share-page', { clipAbove: '.public-link-panel .qr-section' })
})

test('new share', async ({ page }) => {
  await signIn(page, PEOPLE.lukas.email)
  await page.goto('/share/new')
  await page.locator('input[type=file]').first().setInputFiles(
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

test('outbox', async ({ page }) => {
  await signIn(page, PEOPLE.lukas.email)
  await page.goto('/outbox')
  // Every state, not the default "active only", so the pills show.
  await page.locator('select', { has: page.locator('option[value="revoked"]') }).selectOption('')
  await expect(page.getByText(HERO_SUBJECT)).toBeVisible()
  await expect(page.getByText('Service agreement - draft v2')).toBeVisible()
  await shoot(page, 'outbox')
})

test('inbox (client)', async ({ page }) => {
  await signIn(page, PEOPLE.martin.email)
  await page.goto('/inbox')
  await expect(page.getByText(HERO_SUBJECT)).toBeVisible()
  await shoot(page, 'inbox')
})

test('public link page', async ({ page }) => {
  await page.goto(`/d/${demo.heroLinkToken}`)
  await page.locator('input[type=password]').fill(LINK_PASSWORD)
  await page.locator('form.unlock-form button').click()
  await expect(page.getByText('A-101 Ground floor plan rev C.pdf')).toBeVisible()
  await shoot(page, 'public-link')
})

test('approvals', async ({ page }) => {
  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD)
  await page.goto('/approvals')
  await expect(page.getByText(HELD_SUBJECT)).toBeVisible()
  await shoot(page, 'approvals')
})

test('account', async ({ page }) => {
  await signIn(page, PEOPLE.lukas.email)
  await page.goto('/account')
  await expect(page.getByText(PEOPLE.lukas.email).first()).toBeVisible()
  await shoot(page, 'account')
})

test('admin overview', async ({ page }) => {
  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD)
  await page.goto('/admin')
  // All six sidebar categories: v2.17.1 shipped a sidebar showing only
  // "Overview", and nothing that ran before a release rendered it.
  await expect(page.locator('.nav-cat')).toHaveCount(6)
  // The attention tiles and the status line show "…" until they load.
  await expect(page.getByText('…', { exact: true })).toHaveCount(0)
  await shoot(page, 'admin-overview')
})

test('admin users', async ({ page }) => {
  await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD)
  await page.goto('/admin/users')
  await expect(page.getByText(PEOPLE.julia.email)).toBeVisible()
  await shoot(page, 'admin-users')
})

test('share page in German', async ({ page }) => {
  const lukas = await apiLogin(PEOPLE.lukas.email, PASSWORD)
  const setLocale = (locale: 'de' | 'en') =>
    apiFetch(lukas, '/api/account/locale', { method: 'PATCH', body: JSON.stringify({ locale }) })
  expect((await setLocale('de')).ok).toBeTruthy()
  try {
    await signIn(page, PEOPLE.lukas.email)
    await page.goto(`/share/${demo.heroShareId}`)
    await expect(page.getByRole('heading', { name: HERO_SUBJECT })).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('lang', 'de')
    await expect(page.locator('.public-link-panel .qr-svg svg')).toBeVisible()
    await shoot(page, 'share-page.de', { clipAbove: '.public-link-panel .qr-section' })
  } finally {
    await setLocale('en')
  }
})
