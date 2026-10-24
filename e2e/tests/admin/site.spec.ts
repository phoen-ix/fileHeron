import { USER } from '../../helpers'
import { api, contextAs, expect, test, toast, uid } from './_admin'

/* Site › General (site URL, timezone, home page, login banner) and Site ›
 * Branding & legal (logo, surfaces, link, legal pages) - each change is seen
 * where it takes effect, then put back. */

// A 1x1 transparent PNG.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
)

test('site URL: an override is saved and reverted to the env value', async ({ admin: page }) => {
  const url = `https://files-${uid()}.e2e.example`
  try {
    await page.goto('/admin/settings/general')
    const field = page.getByRole('textbox', { name: /^Public site URL/ })
    await field.fill(url)
    await page.locator('form', { has: field }).getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'Site URL saved.')).toBeVisible()
    await expect(page.getByText('Override active')).toBeVisible()
    expect((await api<{ site_url: string }>('/api/admin/settings/site')).site_url).toBe(url)
    await page.getByRole('button', { name: 'Revert to env' }).click()
    await expect(page.getByText('Override active')).toHaveCount(0)
  } finally {
    await api('/api/admin/settings/site', { method: 'PUT', body: JSON.stringify({ site_url: '' }) })
  }
})

test('site timezone: a zone is saved, a bad one cannot be, the browser zone is offered', async ({
  admin: page,
}) => {
  const before = (await api<{ site_timezone: string }>('/api/admin/settings/site')).site_timezone
  try {
    await page.goto('/admin/settings/general')
    const field = page.getByRole('combobox', { name: /^Timezone/ })
    const save = page.locator('form', { has: field }).getByRole('button', { name: 'Save' })
    await field.fill('Mars/Olympus_Mons')
    await expect(save).toBeDisabled()
    await field.fill(before === 'America/New_York' ? 'Asia/Tokyo' : 'America/New_York')
    await save.click()
    await expect(toast(page, 'Site timezone saved.')).toBeVisible()
    await page.reload()
    await expect(field).toHaveValue(
      before === 'America/New_York' ? 'Asia/Tokyo' : 'America/New_York',
    )
    // "Use this" copies the browser's own zone into the field.
    const useBrowser = page.getByRole('button', { name: 'Use this' })
    if (await useBrowser.isEnabled()) {
      await useBrowser.click()
      await expect(field).not.toHaveValue('America/New_York')
    }
  } finally {
    await api('/api/admin/settings/site', {
      method: 'PUT',
      body: JSON.stringify({ site_timezone: before }),
    })
  }
})

test('home page: switched off, / sends a user onward; switched on again', async ({
  admin: page,
  browser,
}) => {
  try {
    await page.goto('/admin/settings/general')
    const toggle = page.getByRole('checkbox', { name: /Enable the default home page/ })
    await expect(toggle).toBeChecked()
    await toggle.uncheck()
    await page.locator('form', { has: toggle }).getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'Home page setting saved.')).toBeVisible()

    const ctx = await contextAs(browser, USER)
    const user = await ctx.newPage()
    await user.goto('/')
    await expect(user).not.toHaveURL(/\/$/)
    await ctx.close()

    await toggle.check()
    await page.locator('form', { has: toggle }).getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'Home page setting saved.')).toBeVisible()
  } finally {
    await api('/api/admin/settings/home-page', {
      method: 'PUT',
      body: JSON.stringify({ enabled: true }),
    })
  }
})

test('login banner: shown on the sign-in page while on, gone when off', async ({
  admin: page,
  browser,
}) => {
  const text = `Maintenance window ${uid()}`
  try {
    await page.goto('/admin/settings/general')
    const toggle = page.getByRole('checkbox', { name: /Show the banner on the login page/ })
    const field = page.getByRole('textbox', { name: /^Banner text/ })
    await toggle.check()
    await field.fill(text)
    await expect(page.getByText(`${500 - text.length} characters remaining.`)).toBeVisible()
    await page.locator('form', { has: field }).getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'Login banner saved.')).toBeVisible()

    const guest = await (await browser.newContext({ baseURL: process.env.E2E_BASE_URL })).newPage()
    await guest.goto('/login')
    await expect(guest.getByRole('note', { name: 'Site announcement' })).toHaveText(text)

    await toggle.uncheck()
    await page.locator('form', { has: field }).getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, 'Login banner saved.')).toBeVisible()
    await guest.reload()
    await expect(guest.getByRole('heading', { name: 'Welcome back.' })).toBeVisible()
    await expect(guest.getByRole('note', { name: 'Site announcement' })).toHaveCount(0)
    await guest.context().close()
  } finally {
    await api('/api/admin/settings/motd', {
      method: 'PUT',
      body: JSON.stringify({ enabled: false, text: '' }),
    })
  }
})

test('branding: a logo is uploaded, shown where enabled, linked, and removed', async ({
  admin: page,
}) => {
  const before = await api<Record<string, unknown>>('/api/admin/settings/branding')
  try {
    await page.goto('/admin/settings/branding')
    await page.getByLabel('Upload logo').setInputFiles({
      name: 'logo.png',
      mimeType: 'image/png',
      buffer: PNG,
    })
    await expect(toast(page, 'Logo uploaded.')).toBeVisible()
    await expect(page.locator('.logo-preview img')).toBeVisible()

    await page.getByRole('checkbox', { name: 'App header (signed-in)' }).check()
    await page.getByRole('checkbox', { name: 'Login page' }).check()
    await page.getByRole('textbox', { name: /^Logo link/ }).fill('https://www.e2e.example/')
    await page.locator('.settings-section').first().getByRole('button', { name: 'Save' }).click()
    await expect(toast(page, /saved/i)).toBeVisible()
    await page.reload()
    await expect(page.getByRole('checkbox', { name: 'Login page' })).toBeChecked()
    await expect(page.getByRole('textbox', { name: /^Logo link/ })).toHaveValue(
      'https://www.e2e.example/',
    )
    // The signed-in header shows it now.
    await expect(page.locator('header img').first()).toBeVisible()

    // A file that is not an image is refused in the page.
    await page.getByLabel('Upload logo').setInputFiles({
      name: 'logo.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('not an image'),
    })
    await expect(page.locator('.toast[data-tone="error"]').first()).toBeVisible()

    await page.getByRole('button', { name: 'Remove' }).click()
    await expect(toast(page, 'Logo removed.')).toBeVisible()
    await expect(page.getByText('No logo')).toBeVisible()
  } finally {
    await api('/api/admin/settings/branding/logo', { method: 'DELETE' }).catch(() => undefined)
    await api('/api/admin/settings/branding', {
      method: 'PUT',
      body: JSON.stringify({
        show_header: before.show_header,
        show_login: before.show_login,
        show_public: before.show_public,
        show_email: before.show_email,
        show_client: before.show_client,
        link_url: before.link_url ?? '',
      }),
    })
  }
})

test('legal pages: written in both languages, linked in the footer, then switched off', async ({
  admin: page,
  browser,
}) => {
  const en = `Imprint text ${uid()}`
  const de = `Impressum Text ${uid()}`
  try {
    await page.goto('/admin/settings/legal')
    await page.getByRole('checkbox', { name: 'Enable Imprint' }).check()
    const editorEn = page.getByRole('textbox', { name: 'Enable Imprint EN' })
    await editorEn.click()
    await page.keyboard.type(en)
    await page.getByRole('button', { name: 'German', exact: true }).click()
    const editorDe = page.getByRole('textbox', { name: 'Enable Imprint DE' })
    await editorDe.click()
    await page.keyboard.type(de)
    // Saved right after the last keystroke: the editor's pending edit must
    // make it into this save (it debounces by 150 ms).
    const first = page.waitForResponse(
      (r) => r.url().endsWith('/api/admin/settings/legal') && r.request().method() === 'PUT',
    )
    await page.getByRole('button', { name: 'Save' }).click()
    expect((await first).ok()).toBeTruthy()
    await expect(toast(page, /saved/i)).toBeVisible()
    const stored = await api<{ imprint: { en: string; de: string } }>('/api/admin/settings/legal')
    expect(stored.imprint.en).toContain(en)
    expect(stored.imprint.de).toContain(de)
    await expect(editorDe).toContainText(de)

    const guestCtx = await browser.newContext({ baseURL: process.env.E2E_BASE_URL })
    const guest = await guestCtx.newPage()
    await guest.goto('/login')
    await guest.getByRole('link', { name: 'Imprint' }).click()
    await expect(guest).toHaveURL(/\/imprint$/)
    await expect(guest.locator('.legal-content')).toContainText(en)
    await guestCtx.close()

    await page.getByRole('checkbox', { name: 'Enable Imprint' }).uncheck()
    const saved = page.waitForResponse(
      (r) => r.url().endsWith('/api/admin/settings/legal') && r.request().method() === 'PUT',
    )
    await page.getByRole('button', { name: 'Save' }).click()
    expect((await saved).ok()).toBeTruthy()
    const after = await api<{ imprint: { enabled: boolean; de: string } }>(
      '/api/admin/settings/legal',
    )
    expect(after.imprint.enabled).toBe(false)
    expect(after.imprint.de).toContain(de)
  } finally {
    await api('/api/admin/settings/legal', {
      method: 'PUT',
      body: JSON.stringify({
        imprint: { enabled: false, en: '', de: '' },
        privacy: { enabled: false, en: '', de: '' },
      }),
    })
  }
})
