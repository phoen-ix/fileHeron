import { spawnSync } from 'node:child_process'
import { randomInt } from 'node:crypto'

import {
  expect,
  test as base,
  type Browser,
  type BrowserContext,
  type Page,
} from '@playwright/test'

import { ADMIN, BASE, apiFetch, apiLogin, freshPassword } from '../../helpers'

/* Shared by the admin-console specs (e2e/COVERAGE.b.md). Every test owns its
 * data (a unique suffix), signs its own browser context in, and fails on an
 * uncaught page error or any 5xx the page received. */

export { expect }

export const BACKEND = process.env.E2E_BACKEND_CONTAINER ?? 'fileheron_e2e-backend'

/** A short unique suffix for names and addresses. */
export function uid(): string {
  return `${Date.now().toString(36)}${randomInt(46_656).toString(36)}`
}

/** Signs a context in through the API: the refresh cookie lands in the
 * context's jar and the SPA's silent refresh picks it up on the next load.
 * (Never a saved storage state - a rotated refresh token replayed trips reuse
 * detection.) */
export async function signIn(ctx: BrowserContext, email: string, password: string): Promise<void> {
  const r = await ctx.request.post('/api/auth/login', { data: { email, password } })
  expect(r.ok(), `sign-in ${email}: ${r.status()}`).toBeTruthy()
}

let cached: { token: string; at: number } | null = null
let twofaCleared = false

/** An admin access token for setup calls, re-minted well inside its 15 min.
 * The first one also clears the 2FA role/group requirement: these specs create
 * employees and clients that must not be sent to enrol, and forced-2fa.spec.ts
 * leaves employees required to (the docs tour clears it the same way). */
export async function adminToken(): Promise<string> {
  if (!cached || Date.now() - cached.at > 10 * 60_000) {
    cached = { token: await apiLogin(ADMIN.email, ADMIN.password), at: Date.now() }
  }
  if (!twofaCleared) {
    const r = await apiFetch(cached.token, '/api/admin/settings/twofa', {
      method: 'PUT',
      body: JSON.stringify({ required_roles: [], required_group_ids: [] }),
    })
    if (!r.ok) throw new Error(`[e2e] clearing the 2FA requirement: ${r.status}`)
    twofaCleared = true
  }
  return cached.token
}

/** A JSON API call as the admin; throws on anything but 2xx. */
export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
  token?: string,
): Promise<T> {
  let r = await apiFetch(token ?? (await adminToken()), path, init)
  if (r.status === 401 && !token) {
    // A journey signed everyone out (a config import): sign in again.
    cached = null
    r = await apiFetch(await adminToken(), path, init)
  }
  const text = await r.text()
  if (!r.ok) throw new Error(`[e2e] ${init.method ?? 'GET'} ${path}: ${r.status} ${text}`)
  return (text ? JSON.parse(text) : undefined) as T
}

export type Role = 'client' | 'employee' | 'admin'
export interface Person {
  id: number
  email: string
  password: string
  name: string
}

/** A fresh account created directly by the admin (no invite round-trip). */
export async function newUser(role: Role, label: string = role): Promise<Person> {
  const s = uid()
  const email = `${label}-${s}@e2e.local`
  const password = freshPassword()
  const name = `E2E ${label} ${s}`
  const u = await api<{ id: number }>('/api/admin/users', {
    method: 'POST',
    body: JSON.stringify({ email, display_name: name, password, target_role: role }),
  })
  return { id: u.id, email, password, name }
}

/** Runs Python inside the backend container (setup the API cannot reach:
 * quarantining a file, ingesting a mail). stdout comes back trimmed. */
export function py(code: string, ...args: string[]): string {
  const r = spawnSync('docker', ['exec', '-i', BACKEND, 'python', '-', ...args], {
    input: code,
    encoding: 'utf8',
  })
  if (r.status !== 0) throw new Error(`[e2e] backend python failed: ${r.stderr}`)
  return r.stdout.trim()
}

/** Collects what makes a page "broken": uncaught errors and 5xx answers. */
export function watch(page: Page): { pageErrors: string[]; serverErrors: string[] } {
  const w = { pageErrors: [] as string[], serverErrors: [] as string[] }
  page.on('pageerror', (e) => w.pageErrors.push(e.message))
  page.on('response', (r) => {
    if (r.status() >= 500) w.serverErrors.push(`${r.status()} ${r.request().method()} ${r.url()}`)
  })
  return w
}

export function expectClean(w: ReturnType<typeof watch>): void {
  expect(w.pageErrors, 'uncaught page errors').toEqual([])
  expect(w.serverErrors, '5xx responses').toEqual([])
}

/** A signed-in context for `who` (default: the seeded admin). */
export async function contextAs(
  browser: Browser,
  who?: { email: string; password: string },
): Promise<BrowserContext> {
  const ctx = await browser.newContext({
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    permissions: ['clipboard-read', 'clipboard-write'],
  })
  await signIn(ctx, (who ?? ADMIN).email, (who ?? ADMIN).password)
  return ctx
}

/** `page` here is a fresh context signed in as the admin; every test fails on
 * a page error or a 5xx. Tests that provoke a 5xx on purpose list it in
 * `allow5xx`. */
export const test = base.extend<{ allow5xx: RegExp[]; admin: Page }>({
  allow5xx: [[], { option: true }],
  admin: async ({ browser, allow5xx }, use) => {
    const ctx = await contextAs(browser)
    const page = await ctx.newPage()
    const w = watch(page)
    await use(page)
    await ctx.close()
    expect(w.pageErrors, 'uncaught page errors').toEqual([])
    expect(
      w.serverErrors.filter((e) => !allow5xx.some((re) => re.test(e))),
      '5xx responses',
    ).toEqual([])
  },
})

/** The admin page's own h1 (AdminPageHeader, or a detail view's title). */
export function heading(page: Page) {
  return page.getByRole('heading', { level: 1 })
}

/** A toast (ToastStack, role=status) carrying `text`. */
export function toast(page: Page, text: RegExp | string) {
  return page.locator('.toast[role="status"]').filter({ hasText: text }).first()
}

/** Answers the app's ConfirmDialog (ui.confirm): the last button confirms. */
export async function confirm(page: Page, yes = true): Promise<void> {
  const d = page.getByRole('dialog')
  await d
    .locator('.actions button')
    .nth(yes ? 1 : 0)
    .click()
  await expect(d).toHaveCount(0)
}

/** Fills the StepUpDialog's password and confirms it. */
export async function stepUp(page: Page, password: string): Promise<void> {
  await page.getByTestId('step-up-password').fill(password)
  await page.getByTestId('step-up-confirm').click()
}

/** A file uploaded by `token`'s owner into a fresh share of their own. */
export async function uploadFile(
  token: string,
  name: string,
  bytes = 'e2e file body',
): Promise<{ shareId: string; fileId: string }> {
  const sh = await api<{ id: string }>(
    '/api/shares',
    {
      method: 'POST',
      body: JSON.stringify({
        kind: 'outbound',
        recipients: { user_ids: [], group_ids: [] },
        expires_at: null,
        subject: `E2E upload ${name}`,
        public_link: { password: null, expires_at: null, max_downloads: null },
      }),
    },
    token,
  )
  const form = new FormData()
  form.append('share_id', sh.id)
  form.append('file', new Blob([bytes], { type: 'text/plain' }), name)
  const r = await fetch(`${BASE}/api/uploads/direct`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}` },
    body: form,
  })
  const text = await r.text()
  if (!r.ok) throw new Error(`[e2e] upload ${name}: ${r.status} ${text}`)
  return { shareId: sh.id, fileId: (JSON.parse(text) as { file_id: string }).file_id }
}

/** A mail in the inbox, through the app's own parser and ingest - there is
 * no IMAP server in the e2e stack. Returns the message id. */
export function ingestMail(subject: string, from = 'user@e2e.local'): number {
  return Number(
    py(
      [
        'import sys, random',
        'from app.database import SessionLocal',
        'from app.services import inbound_mail, inbound_parse',
        'subject, sender = sys.argv[1], sys.argv[2]',
        'raw = (f"From: E2E User <{sender}>\\r\\nTo: files@e2e.local\\r\\nSubject: {subject}\\r\\n"',
        '       "Message-ID: <" + subject.replace(" ", "-") + "@outside.example>\\r\\nMIME-Version: 1.0\\r\\n"',
        '       "Content-Type: multipart/mixed; boundary=b1\\r\\n\\r\\n--b1\\r\\n"',
        '       "Content-Type: multipart/alternative; boundary=b2\\r\\n\\r\\n--b2\\r\\nContent-Type: text/plain\\r\\n\\r\\n"',
        '       "Hello from outside.\\r\\n--b2\\r\\nContent-Type: text/html\\r\\n\\r\\n"',
        '       "<p>Hello from <b>outside</b>.</p>\\r\\n--b2--\\r\\n--b1\\r\\nContent-Type: text/plain; name=note.txt\\r\\n"',
        '       "Content-Disposition: attachment; filename=note.txt\\r\\n\\r\\nattached text\\r\\n--b1--\\r\\n").encode()',
        'db = SessionLocal()',
        'm = inbound_mail.ingest(db, inbound_parse.parse(raw), uid=random.randint(1, 2**31), uidvalidity=4242)',
        'db.commit()',
        'print(m.id)',
      ].join('\n'),
      subject,
      from,
    ),
  )
}
