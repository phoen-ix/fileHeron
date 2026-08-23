import { execSync } from 'node:child_process'

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
export function tokenFromStdout(kind: string): string {
  const logs = execSync(`docker logs ${BACKEND_CONTAINER} 2>&1`, {
    maxBuffer: 128 * 1024 * 1024,
  }).toString()
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
  return `E2e!q${Date.now()}${Math.floor(Math.random() * 1e6)}Zx`
}
