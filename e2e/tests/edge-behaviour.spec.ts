import { expect, test } from '@playwright/test'

import { ADMIN, BASE, apiFetch, apiLogin } from '../helpers'

/* The edge config - docker/frontend/nginx.conf - carries load-bearing production
 * behaviour and had no assertions of any kind (tests-15). `nginx -t` checks
 * syntax; the unit tests in backend/tests/infra check what the file SAYS. Only
 * a request through the running stack checks what it DOES.
 *
 * Also covers the direct-upload branch through the edge (tests-14): the e2e
 * frontend is built with VITE_DIRECT_UPLOAD_THRESHOLD=1 so the browser always
 * takes the resumable path, which left POST /api/uploads/direct - the branch
 * every file under 100 MB takes in production - untested end to end. It is
 * exactly the path the nginx body cap governs, which is why it belongs here.
 *
 * From the 2026-07-30 audit. */

test('a direct upload lands through the edge proxy', async () => {
  const admin = await apiLogin(ADMIN.email, ADMIN.password)

  const sh = await apiFetch(admin, '/api/shares', {
    method: 'POST',
    body: JSON.stringify({
      kind: 'outbound',
      recipients: { user_ids: [], group_ids: [] },
      expires_at: null,
      subject: `E2E direct ${Date.now()}`,
      public_link: { password: null, expires_at: null, max_downloads: null },
    }),
  })
  expect(sh.status).toBe(201)
  const shareId = (await sh.json()).id as string

  // A body big enough to be a real multipart POST rather than a few hundred
  // bytes: nginx's default client_max_body_size is 1 MB, so a cap regression
  // shows up here as a 413 with no error envelope.
  const bytes = Buffer.alloc(2 * 1024 * 1024, 7)
  const form = new FormData()
  form.append('share_id', shareId)
  form.append('file', new Blob([bytes], { type: 'application/octet-stream' }), 'direct.bin')

  const resp = await fetch(`${BASE}/api/uploads/direct`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${admin}` },
    body: form,
  })
  // Read the body ONCE: passing `await resp.text()` as the assertion message
  // consumes it, and the `resp.json()` below then throws "Body is unusable".
  const text = await resp.text()
  expect(resp.status, text).toBe(201)
  const body = JSON.parse(text) as { file_id: string; size_bytes: number }
  expect(body.size_bytes).toBe(bytes.length)
})

test('the SPA shell carries its security headers', async ({ request }) => {
  const resp = await request.get('/')
  expect(resp.status()).toBe(200)
  const h = resp.headers()
  expect(h['x-frame-options']).toBe('DENY')
  expect(h['x-content-type-options']).toBe('nosniff')
  expect(h['referrer-policy']).toBe('strict-origin-when-cross-origin')
})

test('a hashed asset keeps the security headers a location block would drop', async ({
  request,
  page,
}) => {
  // A location-level add_header DISCARDS the server-level ones. /assets/ sets
  // Cache-Control, so it has to re-declare all of them - the trap this config
  // fell into twice.
  await page.goto('/login')
  const assetUrl = await page.evaluate(() => {
    const el = document.querySelector('script[type=module][src^="/assets/"]')
    return el?.getAttribute('src') ?? null
  })
  expect(assetUrl).toBeTruthy()

  const resp = await request.get(assetUrl as string)
  expect(resp.status()).toBe(200)
  const h = resp.headers()
  expect(h['cache-control']).toContain('immutable')
  expect(h['x-content-type-options']).toBe('nosniff')
  expect(h['x-frame-options']).toBe('DENY')
})

test('index.html is never cached', async ({ request }) => {
  // A stale index.html after an in-app Update points at deleted chunk hashes:
  // a blank page that reads as "system down".
  const resp = await request.get('/index.html')
  expect(resp.status()).toBe(200)
  expect(resp.headers()['cache-control']).toContain('no-cache')
})

test('the internal tus-hook receiver is refused at the edge', async ({ request }) => {
  // Defense in depth: /api/internal/* must never be reachable from outside,
  // even if the front proxy's config drifts.
  const resp = await request.post('/api/internal/tus-hooks', {
    data: {},
    failOnStatusCode: false,
  })
  expect(resp.status()).toBe(404)
})

test('a scanner-bait path reaches the backend rather than the SPA shell', async ({
  request,
}) => {
  // The SPA fallback 200s unknown page paths, so a scan would be invisible.
  // These extensions are routed to the backend, which answers the standard
  // 404 envelope - which is what makes the scan land in the error log.
  for (const probe of ['/wp-login.php', '/.env', '/config.bak']) {
    const resp = await request.get(probe, { failOnStatusCode: false })
    expect(resp.status(), probe).toBe(404)
    expect(resp.headers()['content-type'] ?? '', probe).toContain('json')
  }
})

test('the healthcheck answers as plain text, once', async ({ request }) => {
  const resp = await request.get('/healthz')
  expect(resp.status()).toBe(200)
  expect(await resp.text()).toBe('ok')
  expect(resp.headers()['content-type']).toContain('text/plain')
})

test('a CSP is delivered in report-only mode', async ({ request }) => {
  const resp = await request.get('/')
  const h = resp.headers()
  expect(h['content-security-policy-report-only']).toBeTruthy()
  expect(h['content-security-policy-report-only']).toContain("script-src 'self'")
  expect(h['content-security-policy-report-only']).toContain('/api/telemetry/csp-report')
  // Enforcing is a separate, deliberate release step.
  expect(h['content-security-policy']).toBeUndefined()
})
