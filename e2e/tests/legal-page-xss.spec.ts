import { expect, test } from '@playwright/test'

import { ADMIN, apiFetch, apiLogin } from '../helpers'

/* The legal pages render admin-authored HTML through `v-html`
 * (frontend/src/views/LegalPage.vue), and nh3 is the ONLY thing standing
 * between that markup and the DOM.
 *
 * That is not the usual defence-in-depth story, because the CSP protecting this
 * sink is report-only. docker/frontend/nginx.conf serves the SPA shell with
 * `Content-Security-Policy-Report-Only` (enforcement is a deliberate later step
 * once the reports come back empty); the ENFORCING `Content-Security-Policy` in
 * app/middleware/security_headers.py only lands on backend responses, which is
 * not what renders this page. So an inline <script> that survived nh3 would
 * execute, and the report-only policy would merely log it.
 *
 * backend/tests/test_richtext_sanitize.py asserts what nh3 RETURNS. It cannot
 * assert what a browser does with the result - mutation-XSS, where the parser
 * rewrites markup into something executable, lives exactly in that gap. And no
 * e2e test loaded a legal page at all until this one.
 *
 * Sanitisation runs twice (on save, and again on serve at
 * routers/branding.py:108). This test drives the real path: hostile markup in
 * through the admin API, rendered page out. */

const HOSTILE = [
  '<script>window.__xss = "inline-script"</script>',
  '<img src=x onerror="window.__xss = \'img-onerror\'">',
  '<svg><script>window.__xss = "svg-script"</script></svg>',
  '<a href="javascript:window.__xss=\'href\'">click</a>',
  '<iframe src="javascript:window.__xss=\'iframe\'"></iframe>',
  '<body onload="window.__xss = \'body-onload\'">',
  // mXSS: the parser can rewrite this into something executable.
  '<noscript><p title="</noscript><img src=x onerror=window.__xss=\'mxss\'>">',
  '<math><mtext><table><mglyph><style><img src=x onerror=window.__xss="mathml">',
].join('\n')

test('admin-authored HTML on the legal page cannot execute script', async ({ page }) => {
  const admin = await apiLogin(ADMIN.email, ADMIN.password)

  const put = await apiFetch(admin, '/api/admin/settings/legal', {
    method: 'PUT',
    body: JSON.stringify({
      imprint: { enabled: true, en: HOSTILE, de: HOSTILE },
      privacy: { enabled: false, en: '', de: '' },
    }),
  })
  expect(put.ok, `legal PUT failed: ${put.status} ${await put.text()}`).toBeTruthy()

  // A dialog would mean script ran even if the sentinel were overwritten.
  let dialogFired = false
  page.on('dialog', async (d) => {
    dialogFired = true
    await d.dismiss()
  })
  const pageErrors: string[] = []
  page.on('pageerror', (e) => pageErrors.push(String(e)))

  await page.goto('/imprint')
  // The content must actually render - otherwise this passes because the page
  // is blank, which is the vacuous version of this test.
  await expect(page.locator('.legal-content')).toBeVisible()
  await page.waitForTimeout(500) // give any onerror/onload a chance to fire

  expect(dialogFired, 'a dialog opened - script executed').toBeFalsy()
  expect(await page.evaluate(() => (window as unknown as { __xss?: string }).__xss))
    .toBeUndefined()

  // The dangerous constructs must be gone from the DOM, not merely inert.
  const html = await page.locator('.legal-content').innerHTML()
  expect(html.toLowerCase()).not.toContain('<script')
  expect(html.toLowerCase()).not.toContain('onerror')
  expect(html.toLowerCase()).not.toContain('onload')
  expect(html.toLowerCase()).not.toContain('javascript:')
  expect(html.toLowerCase()).not.toContain('<iframe')

  expect(pageErrors, `unexpected page errors: ${pageErrors.join(', ')}`).toHaveLength(0)
})

test('the sanitiser keeps the markup a legal page actually needs', async ({ page }) => {
  /* The control. Every assertion above is satisfied by a sanitiser that strips
   * everything, which would be a broken feature rather than a safe one. */
  const admin = await apiLogin(ADMIN.email, ADMIN.password)
  const benign =
    '<h2>Imprint</h2><p class="text-center">Contact <a href="https://example.com/x">us</a>' +
    ' or <a href="mailto:a@b.c">mail</a>.</p><ul><li>One</li></ul>' +
    '<table><tr><td>cell</td></tr></table>'

  const put = await apiFetch(admin, '/api/admin/settings/legal', {
    method: 'PUT',
    body: JSON.stringify({
      imprint: { enabled: true, en: benign, de: benign },
      privacy: { enabled: false, en: '', de: '' },
    }),
  })
  expect(put.ok).toBeTruthy()

  await page.goto('/imprint')
  const content = page.locator('.legal-content')
  await expect(content).toBeVisible()
  await expect(content.locator('h2')).toHaveText('Imprint')
  await expect(content.locator('a[href="https://example.com/x"]')).toHaveCount(1)
  await expect(content.locator('a[href^="mailto:"]')).toHaveCount(1)
  await expect(content.locator('td')).toHaveText('cell')
  // Alignment survives as a class, never as inline style.
  expect(await content.innerHTML()).not.toContain('style=')
})
