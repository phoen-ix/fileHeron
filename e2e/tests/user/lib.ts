import { spawnSync } from 'node:child_process'
import { randomInt } from 'node:crypto'

import { test as base, expect, type Browser, type Page } from '@playwright/test'

import { ADMIN, BASE, apiFetch, apiLogin, freshPassword } from '../../helpers'

/* Shared setup for the user-side specs (COVERAGE.a.md). Every test makes its
 * own accounts and data with a unique suffix, through the API; only the
 * journey under test goes through the browser. */

const problems: string[] = []

/** Collect what must never happen on a page: an uncaught error or a 5xx. */
export function watch(page: Page): Page {
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
  page.on('response', (r) => {
    if (r.status() >= 500) problems.push(`${r.status()} ${r.request().method()} ${r.url()}`)
  })
  return page
}

/** `test` with every watched page checked after each test. */
export const test = base.extend<{ guard: void }>({
  guard: [
    async ({ page }, use) => {
      problems.length = 0
      watch(page)
      await use()
      expect(problems).toEqual([])
    },
    { auto: true },
  ],
})
export { expect }

export const uid = (): string => `${Date.now().toString(36)}${randomInt(46_656).toString(36)}`

export type Role = 'client' | 'employee' | 'admin'
export interface Persona {
  id: number
  email: string
  password: string
  name: string
  role: Role
}

let adminToken: Promise<string> | null = null
/** The bootstrap admin's access token (15 min; one per spec file is plenty). */
export function admin(): Promise<string> {
  adminToken ??= apiLogin(ADMIN.email, ADMIN.password)
  return adminToken
}

export async function ok<T = unknown>(r: Response | Promise<Response>, what: string): Promise<T> {
  const res = await r
  if (!res.ok) throw new Error(`[e2e] ${what}: ${res.status} ${await res.text()}`)
  return (res.status === 204 ? undefined : await res.json()) as T
}

/** A fresh, verified account made by the admin (no invite round-trip). */
export async function newUser(role: Role, label = role): Promise<Persona> {
  const tag = uid()
  const p = { email: `${label}-${tag}@e2e.example`, password: freshPassword(), name: `${label} ${tag}`, role }
  const created = await ok<{ id: number }>(
    apiFetch(await admin(), '/api/admin/users', {
      method: 'POST',
      body: JSON.stringify({ email: p.email, display_name: p.name, password: p.password, target_role: role }),
    }),
    `create ${p.email}`,
  )
  return { ...p, id: created.id }
}

export const tokenOf = (p: Persona): Promise<string> => apiLogin(p.email, p.password)

/** A group with these members (made by the admin). */
export async function group(name: string, members: number[]): Promise<number> {
  const token = await admin()
  const g = await ok<{ id: number }>(
    apiFetch(token, '/api/groups', { method: 'POST', body: JSON.stringify({ name, description: 'e2e' }) }),
    'create group',
  )
  await ok(
    apiFetch(token, `/api/groups/${g.id}/members`, { method: 'POST', body: JSON.stringify({ user_ids: members }) }),
    'add members',
  )
  return g.id
}

/** An employee may address only the clients they are connected to; sharing a
 * group connects them. */
export async function connect(...people: Persona[]): Promise<void> {
  await group(`E2E team ${uid()}`, people.map((p) => p.id))
}

/** Sign in through the form; ends off /login. */
export async function signIn(page: Page, p: { email: string; password: string }): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('Email', { exact: true }).fill(p.email)
  await page.getByLabel('Password', { exact: true }).fill(p.password)
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page).not.toHaveURL(/\/login(\?|$)/)
}

/** A watched page in a fresh browser context, signed in as `p` (or a guest). */
export async function session(browser: Browser, p?: Persona): Promise<Page> {
  const ctx = await browser.newContext()
  const page = watch(await ctx.newPage())
  if (p) await signIn(page, p)
  return page
}

export interface ShareOpts {
  users?: number[]
  groups?: number[]
  emails?: string[]
  subject?: string
  message?: string
  files?: Record<string, string>
  publicLink?: { password?: string | null; download_limit?: number | null; notify_on_download?: boolean }
  expiresAt?: string | null
  downloadLimit?: number
  notify?: boolean
}

/** A share with its files uploaded (direct path) and announced. */
export async function createShare(token: string, o: ShareOpts = {}): Promise<{ id: string; linkUrl?: string }> {
  const share = await ok<{ id: string; public_link?: { url: string } | null }>(
    apiFetch(token, '/api/shares', {
      method: 'POST',
      body: JSON.stringify({
        kind: 'outbound',
        recipients: { user_ids: o.users ?? [], group_ids: o.groups ?? [], emails: o.emails ?? [] },
        expires_at: o.expiresAt ?? null,
        subject: o.subject ?? `E2E share ${uid()}`,
        message: o.message ?? null,
        public_link: o.publicLink ?? null,
        download_limit: o.downloadLimit ?? null,
        notify_recipients: o.notify ?? null,
      }),
    }),
    'create share',
  )
  const ids: string[] = []
  for (const [name, text] of Object.entries(o.files ?? {})) ids.push(await upload(token, share.id, name, text))
  if (ids.length) {
    await ok(
      apiFetch(token, `/api/shares/${share.id}/files-added`, {
        method: 'POST',
        body: JSON.stringify({ notify: true, file_ids: ids }),
      }),
      'announce files',
    )
  }
  return { id: share.id, linkUrl: share.public_link?.url }
}

export async function upload(token: string, shareId: string, name: string, text: string): Promise<string> {
  const form = new FormData()
  form.append('share_id', shareId)
  form.append('file', new Blob([text], { type: 'text/plain' }), name)
  const r = await ok<{ file_id: string }>(
    fetch(`${BASE}/api/uploads/direct`, { method: 'POST', headers: { authorization: `Bearer ${token}` }, body: form }),
    `upload ${name}`,
  )
  return r.file_id
}

/** PUT an admin settings page, returning what it held before (to restore). */
export async function setSettings(path: string, patch: Record<string, unknown>): Promise<Record<string, unknown>> {
  const token = await admin()
  const before = await ok<Record<string, unknown>>(apiFetch(token, path), `read ${path}`)
  await ok(apiFetch(token, path, { method: 'PUT', body: JSON.stringify({ ...before, ...patch }) }), `write ${path}`)
  return before
}

export async function restoreSettings(path: string, before: Record<string, unknown>): Promise<void> {
  await ok(apiFetch(await admin(), path, { method: 'PUT', body: JSON.stringify(before) }), `restore ${path}`)
}

const BACKEND = process.env.E2E_BACKEND_CONTAINER ?? 'fileheron_e2e-backend'
const WORKER = BACKEND.replace(/-backend$/, '-worker')

/** How long a share's announcement (its recipients' mail and bell) may take.
 * It goes out when the upload's scan or the owner's files-added call finds the
 * share ready; when the two interleave, each can see the other's change still
 * uncommitted, and the minute sweep (`announce_ready_shares`, the designed
 * fallback) sends it instead - up to a minute later. */
export const ANNOUNCED_WITHIN = 75_000

/** The newest one-time link `/<kind>/<token>` mailed to `email` (printed by the
 * backend or the worker with SMTP unset - the mail log masks these tokens).
 * Polls: the worker sends notification mails a moment after the request. */
export async function mailedToken(kind: string, email: string, timeout = 20_000): Promise<string> {
  let found = ''
  await expect
    .poll(
      () => {
        for (const c of [BACKEND, WORKER]) {
          const r = spawnSync('docker', ['logs', c], { maxBuffer: 256 * 1024 * 1024, encoding: 'utf8' })
          if (r.status !== 0) throw new Error(`[e2e] docker logs ${c}: ${r.stderr}`)
          // One printed mail: "EMAIL DEV -> <to> ..." up to the next one.
          for (const block of (r.stderr + r.stdout).split('EMAIL DEV').filter((b) => b.includes(email))) {
            const m = [...block.matchAll(new RegExp(`/${kind}/([A-Za-z0-9._~-]+)`, 'g'))]
            if (m.length) found = m[m.length - 1][1]
          }
        }
        return found
      },
      { timeout, message: `a /${kind}/ link mailed to ${email}` },
    )
    .not.toBe('')
  return found
}
