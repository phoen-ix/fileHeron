import type { Browser, Locator, Page } from '@playwright/test'

import { ADMIN, BASE, apiFetch, totpCode } from '../../helpers'
import { py } from '../admin/_admin'
import {
  ANNOUNCED_WITHIN,
  admin,
  connect,
  createShare,
  expect,
  expectedRefusal,
  forgetAdmin,
  group,
  mailed,
  mailedToken,
  newUser,
  ok,
  pickRecipient,
  restoreSettings,
  setSettings,
  signIn,
  test,
  tokenOf,
  uid,
  watch,
  type Persona,
  type ShareOpts,
} from '../user/lib'

/* Use cases: a chain in which one person hands something to another - an
 * account, a mailed link, a setting - and the second person USES it
 * (COVERAGE.c.md).
 *
 * The other specs test a page's features and set their preconditions up
 * through the API, so what one page produces was rarely what the next one
 * consumed: an account the admin created in the UI was "signed in" with an API
 * call, a forced password reset ended at "a token is shown". Of 22 such
 * hand-overs, two were followed to the second person (2026-11-21).
 *
 * The rules here, pinned by backend/tests/test_frontend_test_hygiene.py:
 * - Every person acts in the browser and signs in through the FORM - the
 *   admin too (`browserOf(browser, THE_ADMIN)`).
 * - What is handed over is the SAME thing that is used: the link on the
 *   admin's screen, or the mail to that address (`mailedToken`).
 * - The outcome is read on the affected person's screen.
 * - A use-case spec calls no API. Facts a use case needs but is not about - a
 *   second account, a file - come from `given` below, the one place that does. */

export { ANNOUNCED_WITHIN, expect, expectedRefusal, mailed, mailedToken, pickRecipient, signIn, test, uid }
export type { Persona }

/** The seeded admin. Signs in through the form like everyone else. */
export const THE_ADMIN = { email: ADMIN.email, password: ADMIN.password }

/** A person's own browser - their own cookies - on a page watched for errors;
 * signed in through the form when `who` is given. */
export async function browserOf(
  browser: Browser,
  who?: { email: string; password: string },
): Promise<Page> {
  const page = watch(await (await browser.newContext()).newPage())
  if (who) await signIn(page, who)
  return page
}

/** Background facts, made through the API: what a use case needs but is not
 * about. Never the act under test, and never the check of its outcome. */
export const given = {
  /** An account that already exists (admin-made, verified). */
  user: newUser,
  /** A group with these members. */
  group,
  /** People who may address each other (they share a group). */
  connected: connect,
  /** A share `owner` already sent, with its files scanned. */
  share: async (owner: Persona, opts: ShareOpts = {}) => createShare(await tokenOf(owner), opts),
  /** A setting as it stood before, to put back afterwards. */
  settings: setSettings,
  settingsBack: restoreSettings,
  /** The branding as it stands; call what this returns to put it back. Its
   * GET carries the logo's facts, which its PUT refuses, and the logo has a
   * route of its own - so `settings`/`settingsBack` cannot restore it. */
  brandingKept: async (): Promise<() => Promise<void>> => {
    const path = '/api/admin/settings/branding'
    const token = await admin()
    const before = await ok<Record<string, unknown>>(apiFetch(token, path), 'read the branding')
    const { logo, ...switches } = before
    return async () => {
      const again = await admin()
      const hadLogo = (logo as { present?: boolean } | undefined)?.present === true
      if (!hadLogo) await apiFetch(again, `${path}/logo`, { method: 'DELETE' })
      await ok(
        apiFetch(again, path, { method: 'PUT', body: JSON.stringify({ ...switches, link_url: switches.link_url ?? '' }) }),
        'restore the branding',
      )
    }
  },
  /** A share `owner` sent whose one file the scanner then flagged. The e2e
   * stack runs without ClamAV, so the file is quarantined with the worker's
   * own call, exactly as an infected verdict would (admin/quarantine.spec.ts). */
  infectedShare: async (owner: Persona, opts: ShareOpts & { fileName: string }) => {
    const share = await createShare(await tokenOf(owner), { ...opts, files: { [opts.fileName]: 'payload' } })
    const detail = await ok<{ files: Array<{ id: string }> }>(
      apiFetch(await tokenOf(owner), `/api/shares/${share.id}`),
      'read the share',
    )
    py(
      [
        'import sys',
        'from app.database import SessionLocal',
        'from app.models.file import File',
        'from app.services.quarantine import quarantine_file',
        'db = SessionLocal()',
        'quarantine_file(db, file=db.get(File, sys.argv[1]), signature="Eicar-Test-Signature")',
        'db.commit()',
      ].join('\n'),
      detail.files[0].id,
    )
    return share
  },
}

/** Answer the "confirm with your password" dialog. */
export async function stepUp(page: Page, password: string): Promise<void> {
  await page.getByTestId('step-up-password').fill(password)
  await page.getByTestId('step-up-confirm').click()
}

/** What a program holding `token` is told when it asks who it is. An API
 * token's holder is a script, not a person at a screen: the API's answer IS
 * what it sees, so this is the one place a use case reads the API. */
export async function asTokenHolder(token: string): Promise<{ status: number; email?: string; code?: string }> {
  const r = await fetch(`${BASE}/api/account/me`, { headers: { authorization: `Bearer ${token}` } })
  const body = (await r.json()) as { email?: string; code?: string }
  return r.ok ? { status: r.status, email: body.email } : { status: r.status, code: body.code }
}

/** After a config import with accounts: every session is over, the helpers'
 * own included. */
export const everySessionEnded = forgetAdmin

/** The six digits a person's authenticator app shows for `secret`. */
export const authenticatorCode = totpCode

/** A file for an upload field. */
export const file = (name: string, text: string) => ({
  name,
  mimeType: 'text/plain',
  buffer: Buffer.from(text),
})

/** Click `trigger` and read what the browser downloads. */
export async function download(page: Page, trigger: Locator): Promise<{ name: string; text: string }> {
  const [dl] = await Promise.all([page.waitForEvent('download'), trigger.click()])
  const chunks: Buffer[] = []
  for await (const c of await dl.createReadStream()) chunks.push(c as Buffer)
  return { name: dl.suggestedFilename(), text: Buffer.concat(chunks).toString('latin1') }
}

export async function signOut(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Menu' }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(page).toHaveURL(/\/login(\?|$)/)
}

/** Fill in the sign-in form and submit; what happens next is the test's to say. */
export async function tryToSignIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Continue' }).click()
}

/** The admin finds a person on the Users page and opens them. */
export async function openUser(adminPage: Page, email: string): Promise<void> {
  await adminPage.goto('/admin/users')
  await adminPage.getByRole('searchbox', { name: 'Search by name or email…' }).fill(email)
  const rows = adminPage.locator('.user-table tbody tr')
  await expect(rows).toHaveCount(1)
  await rows.first().click()
  await expect(adminPage).toHaveURL(/\/admin\/users\/\d+$/)
}

/** The app's ConfirmDialog: the last button confirms. */
export async function confirm(page: Page): Promise<void> {
  const dialog = page.getByRole('dialog')
  await dialog.locator('.actions button').last().click()
  await expect(dialog).toHaveCount(0)
}

export function toast(page: Page, text: RegExp | string) {
  return page.locator('.toast[role="status"]').filter({ hasText: text }).first()
}

/** The path of a link shown on a page (the app's own origin dropped), to be
 * opened in another person's browser. */
export function pathOf(url: string): string {
  const u = new URL(url.trim())
  return u.pathname + u.search + u.hash
}
