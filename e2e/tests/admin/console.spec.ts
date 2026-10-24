import { readFileSync } from 'node:fs'

import { BASE } from '../../helpers'
import { api, expect, newUser, test, uid, uploadFile } from './_admin'

/* The console around the pages: the Overview (setting search, attention
 * tiles, status, recent setting changes, all pages), the sidebar (categories
 * open and close, and remember it), the tab bar of a tab page, the
 * breadcrumbs, and Analytics (ranges, CSV export). */

test.describe('overview', () => {
  test('the setting search finds a setting by keyword and jumps to it', async ({ admin: page }) => {
    await page.goto('/admin')
    const search = page.getByRole('combobox', { name: 'Find a setting' })
    await search.fill('zzzz-nothing')
    await expect(page.getByText('Nothing matches.')).toBeVisible()
    await search.fill('timezone')
    const options = page.getByRole('listbox', { name: 'Search results' }).getByRole('option')
    await expect(options.first()).toBeVisible()
    await search.press('ArrowDown')
    await search.press('ArrowUp')
    await search.press('Enter')
    await expect(page).toHaveURL(/\/admin\/settings\/general/)
  })

  test('tiles, status and every page card lead to their pages', async ({ admin: page }) => {
    await page.goto('/admin')
    const tiles = page.locator('.ov-tile')
    await expect(tiles.first()).toBeVisible()
    await page.locator('.ov-tile', { hasText: 'Quarantined files' }).click()
    await expect(page).toHaveURL(/\/admin\/quarantine$/)

    await page.goto('/admin')
    await page.locator('.ov-health-item', { hasText: /Running/ }).click()
    await expect(page).toHaveURL(/\/admin\/system$/)

    await page.goto('/admin')
    const section = page.locator('section', {
      has: page.getByRole('heading', { name: 'All pages' }),
    })
    await expect(section.getByRole('link').first()).toBeVisible()
    const hrefs = await section
      .getByRole('link')
      .evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).getAttribute('href')))
    expect(hrefs.length).toBeGreaterThanOrEqual(30)
    for (const h of hrefs) expect(h).toMatch(/^\/admin\//)
    await section.getByRole('link', { name: 'Webhooks' }).click()
    await expect(page).toHaveURL(/\/admin\/settings\/webhooks$/)
  })

  test('a settings change shows up under "Recently changed settings"', async ({ admin: page }) => {
    const text = `Overview banner ${uid()}`
    try {
      await api('/api/admin/settings/motd', {
        method: 'PUT',
        body: JSON.stringify({ enabled: false, text }),
      })
      await page.goto('/admin')
      const changes = page.getByTestId('ov-changes')
      await expect(changes.locator('li').first()).toBeVisible()
      await changes.locator('li').first().getByRole('link').click()
      await expect(page).toHaveURL(/\/admin\//)
      await page.goto('/admin')
      await page.getByRole('link', { name: 'Full audit log' }).click()
      await expect(page).toHaveURL(/\/admin\/audit-log$/)
    } finally {
      await api('/api/admin/settings/motd', {
        method: 'PUT',
        body: JSON.stringify({ enabled: false, text: '' }),
      })
    }
  })
})

test.describe('sidebar, tabs and breadcrumbs', () => {
  test('a category closes and opens, and the choice survives a reload', async ({ admin: page }) => {
    await page.goto('/admin')
    const nav = page.getByRole('navigation', { name: 'Admin navigation' })
    const header = nav.getByRole('button', { name: 'Site & appearance' }).filter({ visible: true })
    const before = await header.getAttribute('aria-expanded')
    await header.click()
    const after = before === 'true' ? 'false' : 'true'
    await expect(header).toHaveAttribute('aria-expanded', after)
    // Stored per account (PATCH /api/account/admin-nav-open).
    await expect
      .poll(async () => {
        await page.reload()
        return nav
          .getByRole('button', { name: 'Site & appearance' })
          .filter({ visible: true })
          .getAttribute('aria-expanded')
      })
      .toBe(after)
    await header.click()
    await expect(header).toHaveAttribute('aria-expanded', before ?? 'false')
  })

  test('a sidebar link navigates and marks itself current', async ({ admin: page }) => {
    await page.goto('/admin')
    const nav = page.getByRole('navigation', { name: 'Admin navigation' })
    const people = nav.getByRole('button', { name: 'People & access' }).filter({ visible: true })
    if ((await people.getAttribute('aria-expanded')) !== 'true') await people.click()
    await nav.getByRole('link', { name: 'Groups' }).filter({ visible: true }).click()
    await expect(page).toHaveURL(/\/admin\/groups$/)
    await expect(
      nav.getByRole('link', { name: 'Groups' }).filter({ visible: true }),
    ).toHaveAttribute('aria-current', 'page')
  })

  test('the tabs of a tab page switch pages; breadcrumbs lead back', async ({ admin: page }) => {
    await page.goto('/admin/sessions')
    const tabs = page.getByRole('tablist')
    await tabs.getByRole('tab', { name: 'Policy' }).click()
    await expect(page).toHaveURL(/\/admin\/settings\/sessions$/)
    await expect(tabs.getByRole('tab', { name: 'Policy' })).toHaveAttribute('aria-selected', 'true')
    await tabs.getByRole('tab', { name: 'Active' }).focus()
    await page.keyboard.press('ArrowRight')
    await tabs.getByRole('tab', { name: 'Active' }).click()
    await expect(page).toHaveURL(/\/admin\/sessions$/)

    const crumbs = page.getByRole('navigation', { name: 'Breadcrumb' })
    await crumbs.getByRole('link').first().click()
    await expect(page).toHaveURL(/\/admin$/)
  })
})

test.describe('analytics', () => {
  test('each range loads; the CSV export carries the range', async ({ admin: page }) => {
    await page.goto('/admin/analytics')
    const ranges = page.getByRole('group', { name: 'Time range' }).getByRole('button')
    await expect(ranges.first()).toBeVisible()
    const n = await ranges.count()
    expect(n).toBeGreaterThan(1)
    for (let i = 0; i < n; i++) {
      await ranges.nth(i).click()
      await expect(ranges.nth(i)).toHaveClass(/active/)
      await expect(page.getByRole('heading', { name: 'Shares created' })).toBeVisible()
    }
    const days = ((await ranges.nth(n - 1).textContent()) ?? '').match(/\d+/)![0]
    const dl = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Export CSV' }).click()
    const d = await dl
    expect(d.suggestedFilename()).toBe(`analytics-${days}d.csv`)
    expect(readFileSync(await d.path(), 'utf8').length).toBeGreaterThan(0)
    for (const h of [
      'Storage used',
      'Downloads',
      'Quarantines',
      'Files by state',
      'Top uploaders',
      'Most-downloaded shares',
    ]) {
      await expect(page.getByRole('heading', { name: h })).toBeVisible()
    }
  })

  test('a user over 90% of their quota is listed under Quota warnings', async ({ admin: page }) => {
    const u = await newUser('employee', 'quota')
    await api(`/api/admin/users/${u.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ quota_bytes: 14 }),
    })
    const r = await fetch(`${BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: u.email, password: u.password }),
    })
    await uploadFile((await r.json()).access_token as string, 'thirteen.txt', '13 bytes here')
    await page.goto('/admin/analytics')
    const card = page.locator('section.card', {
      has: page.getByRole('heading', { name: 'Quota warnings' }),
    })
    await expect(card.locator('tr', { hasText: u.email })).toBeVisible()
  })
})
