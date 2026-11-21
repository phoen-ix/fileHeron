import { spawnSync } from 'node:child_process'
import { randomInt } from 'node:crypto'

import { expect, type Page } from '@playwright/test'
import { generateSync } from 'otplib'

/* Shared helpers + the deterministic seeded accounts (docker-compose.e2e.yml). */

export const ADMIN = { email: 'admin@e2e.local', password: 'AdminPass123!' }
export const USER = { email: 'user@e2e.local', password: 'UserPass123!' }

export const BASE = process.env.E2E_BASE_URL ?? 'http://localhost:8080'
const BACKEND_CONTAINER = process.env.E2E_BACKEND_CONTAINER ?? 'fileheron_e2e-backend'

/** Scrape a one-time link token from the backend container's stdout. With SMTP
 * unset the backend prints an "EMAIL DEV ->" block containing the LIVE token
 * (the mail-log API masks these, so stdout is the only source). `kind` is the
 * URL path segment: 'register' | 'reset-password' | 'verify-email'. Newest wins. */
export function tokenFromStdout(kind: string, container = BACKEND_CONTAINER): string {
  // No shell: the container name comes from the environment. stderr goes first
  // so stdout, where both print() and the JSON log handler write, stays newest.
  const r = spawnSync('docker', ['logs', container], {
    maxBuffer: 128 * 1024 * 1024,
    encoding: 'utf8',
  })
  if (r.error) throw r.error
  if (r.status !== 0) throw new Error(`[e2e] docker logs ${container} exited ${r.status}: ${r.stderr}`)
  const logs = r.stderr + r.stdout
  const matches = [...logs.matchAll(new RegExp(`/${kind}/([A-Za-z0-9._~-]+)`, 'g'))].map((m) => m[1])
  if (matches.length === 0) throw new Error(`[e2e] no /${kind}/ token in backend stdout`)
  return matches[matches.length - 1]
}

/** Log in via the API and return the access token (for setup steps that aren't
 * the journey under test - e.g. an admin minting an invite). */
export async function apiLogin(email: string, password: string): Promise<string> {
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  if (!r.ok) throw new Error(`[e2e] apiLogin ${email} failed: ${r.status}`)
  return (await r.json()).access_token as string
}

/** The brand the instance reports (`branding.app_name`, else env `APP_NAME`).
 * Every tab title ends in it, and the e2e stack runs on the env default. */
export async function appName(): Promise<string> {
  const r = await fetch(`${BASE}/api/config-public`)
  if (!r.ok) throw new Error(`[e2e] config-public failed: ${r.status}`)
  return (await r.json()).app_name as string
}

/** Authenticated JSON request against the API with a bearer token. */
export async function apiFetch(
  token: string,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'content-type': 'application/json',
      authorization: `Bearer ${token}`,
      ...(init.headers ?? {}),
    },
  })
}

/** Invite (as admin) + register a fresh user via the API, returning its creds.
 * Setup helper for journeys that need an isolated account (2FA, forced-2FA). */
export async function createUser(
  adminToken: string,
  opts: { email: string; password: string; role: 'client' | 'employee'; displayName?: string },
): Promise<{ email: string; password: string }> {
  const inv = await apiFetch(adminToken, '/api/account/invite', {
    method: 'POST',
    body: JSON.stringify({
      email: opts.email,
      target_role: opts.role,
      display_name_hint: opts.displayName ?? opts.email,
    }),
  })
  if (!inv.ok) throw new Error(`[e2e] invite ${opts.email} failed: ${inv.status} ${await inv.text()}`)
  const token = tokenFromStdout('register')
  const reg = await fetch(`${BASE}/api/auth/register-from-invite`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      token,
      password: opts.password,
      display_name: opts.displayName ?? opts.email,
      locale: 'en',
    }),
  })
  if (!reg.ok) throw new Error(`[e2e] register ${opts.email} failed: ${reg.status} ${await reg.text()}`)
  return { email: opts.email, password: opts.password }
}

/** The current TOTP code for a base32 secret.
 *
 * The ONE place this suite touches otplib, so the library's shape lives in a
 * single file. Codes must match what the backend accepts, and the backend
 * verifies with **pyotp** (`services/totp.py`), a different library entirely -
 * so this is a cross-language contract, not just a call. Verified equal to
 * `pyotp.TOTP(secret).now()` for a 20-byte secret in the same 30s step.
 *
 * otplib 13 enforces MIN_SECRET_BYTES = 16 and throws SecretTooShortError
 * below it - including for otplib's OWN documented example secret
 * ('JBSWY3DPEHPK3PXP', 10 bytes). Real secrets are fine: the backend mints
 * `pyotp.random_base32()` = 32 base32 chars = 20 bytes, and that one call at
 * `services/totp.py:88` is the only mint site. A hand-written short secret in
 * a future test would fail here for a reason the error message explains but
 * nothing else would.
 */
export function totpCode(secret: string): string {
  return generateSync({ secret })
}

/** Enroll TOTP for a user (setup -> enable with a computed code). Returns the
 * base32 secret so the caller can generate login codes. */
export async function enroll2FA(email: string, password: string): Promise<string> {
  const token = await apiLogin(email, password)
  const s = await apiFetch(token, '/api/account/2fa/setup', { method: 'POST' })
  if (!s.ok) throw new Error(`[e2e] 2fa setup failed: ${s.status}`)
  const secret = (await s.json()).secret_b32 as string
  const e = await apiFetch(token, '/api/account/2fa/enable', {
    method: 'POST',
    body: JSON.stringify({ code: totpCode(secret) }),
  })
  if (!e.ok) throw new Error(`[e2e] 2fa enable failed: ${e.status} ${await e.text()}`)
  return secret
}

/** A strong, almost-certainly-not-breached password (HIBP is enforced on
 * register). Unique per call so re-runs don't collide. */
export function freshPassword(): string {
  return `E2e!q${Date.now()}${randomInt(1_000_000)}Zx`
}

/** Send every browser upload on `page` of at least `ceiling` bytes (default:
 * all) down the resumable (tus) path. The SPA uploads a file directly when it
 * is under the server's direct-upload ceiling (`max_direct_upload_bytes` in
 * /api/config-public, 100 MB by default), and that live value wins over the
 * frontend's build-time threshold, so this answers the page with `ceiling`.
 * The server keeps its own, so API seeding through /api/uploads/direct still
 * works. Call before the page loads. */
export async function forceResumableUploads(page: Page, ceiling = 1): Promise<void> {
  await page.route('**/api/config-public', async (route) => {
    const response = await route.fetch()
    const json = await response.json()
    await route.fulfill({ response, json: { ...json, max_direct_upload_bytes: ceiling } })
  })
}

/** A field takes a click only on its label's words and on its control (the
 * SPA's utils/fieldLabels.ts, and `width: fit-content` on the label text). A
 * wrapping <label> made the whole block one target, as wide as its column, so
 * a click on the help text or beside the label jumped into the field
 * (2026-11-21).
 *
 * With the real mouse, on up to `max` fields of the page as it stands,
 * whatever class they are built with: beside the label and on the help text
 * the field must NOT take the focus; on the label's words it must. Left out:
 * checkbox and radio rows (the whole row is their target on purpose) and
 * search fields that open a list on focus (their own journeys check them; the
 * list would cover the next field). Returns how many fields were probed. */
export async function expectPreciseFieldLabels(page: Page, max = 3): Promise<number> {
  const labels = page.locator('label:visible')
  const total = await labels.count()
  let probed = 0
  for (let i = 0; i < total && probed < max; i++) {
    const label = labels.nth(i)
    const spots = await label.evaluate((el: HTMLLabelElement) => {
      const control = el.control as HTMLInputElement | null
      if (!control || control.disabled || document.activeElement === control) return null
      const tag = control.tagName
      const textLike =
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        (tag === 'INPUT' && !['checkbox', 'radio', 'file', 'hidden', 'range', 'color'].includes(control.type))
      if (!textLike || control.getAttribute('role') === 'combobox') return null
      // The element holding the label's words: the label itself when it points
      // at its field from outside, else its first child that is neither the
      // control nor help text.
      const wraps = el.contains(control)
      const text = wraps
        ? [...el.children].find(
            (c) => c !== control && !c.contains(control) && !c.matches('.fh-field-help, .fh-field-error, ul, button'),
          )
        : el
      if (!text || !(text.textContent ?? '').trim()) return null
      el.scrollIntoView({ block: 'center' })
      // The row the label sits in: the wrapping label, or the other label's parent.
      const row = (wraps ? el : el.parentElement!).getBoundingClientRect()
      // The WORDS, not the element's box: a label stretched across its row is
      // the defect, and its box would then leave no "beside" to click.
      const words = document.createRange()
      words.selectNodeContents(text)
      const t = words.getBoundingClientRect()
      const help = el.querySelector<HTMLElement>(':scope > .fh-field-help')?.getBoundingClientRect()
      const y = t.top + t.height / 2
      const c = control.getBoundingClientRect()
      // Beside the words, on their line - unless the control sits there (a
      // one-line field).
      const x = row.right - 6
      const clear = row.right - t.right > 16 && !(x >= c.left && x <= c.right && y >= c.top && y <= c.bottom)
      return {
        name: (text.textContent ?? '').trim(),
        beside: clear ? { x, y } : null,
        help:
          help && help.width > 12 && help.height > 4
            ? { x: help.left + 6, y: help.top + help.height / 2 }
            : null,
        text: { x: t.left + Math.min(8, t.width / 2), y },
      }
    })
    if (!spots) continue
    probed++
    const focused = () =>
      label.evaluate((el: HTMLLabelElement) => document.activeElement === el.control)
    for (const [where, spot, takes] of [
      ['beside the label', spots.beside, false],
      ['on the help text', spots.help, false],
      ['on the label text', spots.text, true],
    ] as const) {
      if (!spot) continue
      await page.mouse.click(spot.x, spot.y)
      expect(
        await focused(),
        `"${spots.name}" on ${new URL(page.url()).pathname}: a click ${where} ${takes ? 'must' : 'must not'} focus the field`,
      ).toBe(takes)
    }
  }
  return probed
}
