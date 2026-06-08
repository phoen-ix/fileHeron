import { execSync } from 'node:child_process'

/* Shared helpers + the deterministic seeded accounts (docker-compose.e2e.yml). */

export const ADMIN = { email: 'admin@e2e.local', password: 'AdminPass123!' }
export const USER = { email: 'user@e2e.local', password: 'UserPass123!' }

const BASE = process.env.E2E_BASE_URL ?? 'http://localhost:8080'
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
