import { ADMIN, BASE, apiFetch, apiLogin } from '../helpers'

/* The demo organisation the docs tour photographs. Seeded through the public
 * API only, so the tour needs no docker CLI and runs against any stack: the
 * docs stack (docker-compose.docs.yml, where the bootstrap admin is Anna's
 * address) or the e2e stack after its specs (the render check in e2e.yml).
 *
 * IDEMPOTENT: every user, group and share is looked up before it is created,
 * so a second run against the same stack reuses what the first one made.
 * That is also why the passwords are fixed rather than `freshPassword()` - a
 * re-run has to be able to sign in as the users a previous run created. */

export const ADMIN_EMAIL = process.env.DOCS_ADMIN_EMAIL ?? ADMIN.email
export const ADMIN_PASSWORD = process.env.DOCS_ADMIN_PASSWORD ?? ADMIN.password
export const PASSWORD = 'Heron-docs-tour-7Kq2!'
export const LINK_PASSWORD = 'riverside-rev-c'

type Role = 'admin' | 'employee' | 'client'
export interface Persona {
  email: string
  displayName: string
  role: Role
}

export const PEOPLE = {
  lukas: { email: 'lukas.wagner@heron-demo.example', displayName: 'Lukas Wagner', role: 'employee' },
  sophie: { email: 'sophie.huber@heron-demo.example', displayName: 'Sophie Huber', role: 'employee' },
  thomas: { email: 'thomas.gruber@heron-demo.example', displayName: 'Thomas Gruber', role: 'employee' },
  martin: {
    email: 'martin.keller@keller-architekten.example',
    displayName: 'Martin Keller',
    role: 'client',
  },
  julia: { email: 'julia.novak@novak-legal.example', displayName: 'Julia Novak', role: 'client' },
  elena: { email: 'elena.rossi@studio-rossi.example', displayName: 'Elena Rossi', role: 'client' },
} satisfies Record<string, Persona>

const MB = 1024 * 1024

interface DemoFile {
  name: string
  bytes: number
  type: string
}

interface DemoShare {
  subject: string
  message?: string
  files: DemoFile[]
  to: { users?: Array<keyof typeof PEOPLE>; groups?: string[] }
  expiresInDays: number | null
  publicLink?: { password: string; download_limit: number }
  end?: 'expire' | 'revoke'
}

const RIVERSIDE = 'Project Riverside'
const NOVAK = 'Novak Legal'

/** The hero: the share the README opens with. */
export const HERO_SUBJECT = 'Riverside - construction drawings, rev C'

const LUKAS_SHARES: DemoShare[] = [
  // Oldest first: the outbox sorts newest first, so the hero ends up on top.
  {
    subject: 'Kick-off meeting minutes',
    files: [{ name: 'Kick-off minutes 2026-08-04.docx', bytes: 0.2 * MB, type: 'application/msword' }],
    to: { groups: [RIVERSIDE] },
    expiresInDays: 14,
    end: 'expire',
  },
  {
    subject: 'Service agreement - draft v2',
    files: [{ name: 'Service agreement draft v2.docx', bytes: 0.18 * MB, type: 'application/msword' }],
    to: { users: ['julia'] },
    expiresInDays: 30,
    end: 'revoke',
  },
  {
    subject: 'Tender package',
    files: [
      { name: 'Tender package - Riverside.zip', bytes: 22.4 * MB, type: 'application/zip' },
      { name: 'Bill of quantities.xlsx', bytes: 0.9 * MB, type: 'application/vnd.ms-excel' },
    ],
    to: { users: ['martin'] },
    expiresInDays: null,
  },
  {
    subject: 'Q3 status report',
    message: 'Julia, the Q3 report and the figures behind it. Happy to walk you through it on Thursday.',
    files: [
      { name: 'Q3 status report.pdf', bytes: 1.8 * MB, type: 'application/pdf' },
      { name: 'Financials Q3 2026.xlsx', bytes: 0.64 * MB, type: 'application/vnd.ms-excel' },
    ],
    to: { users: ['julia'] },
    expiresInDays: 30,
  },
  {
    subject: HERO_SUBJECT,
    message:
      'Hi Martin, here is revision C of the drawings. Changes against rev B are clouded on each sheet; ' +
      'the structural model is updated to match.',
    files: [
      { name: 'A-101 Ground floor plan rev C.pdf', bytes: 4.2 * MB, type: 'application/pdf' },
      { name: 'A-102 First floor plan rev C.pdf', bytes: 3.9 * MB, type: 'application/pdf' },
      { name: 'A-301 Sections rev C.pdf', bytes: 2.7 * MB, type: 'application/pdf' },
      { name: 'Riverside structural model.ifc', bytes: 38.6 * MB, type: 'application/octet-stream' },
    ],
    to: { users: ['martin'], groups: [RIVERSIDE] },
    expiresInDays: 14,
    publicLink: { password: LINK_PASSWORD, download_limit: 10 },
  },
]

const MARTIN_INBOUND: DemoShare = {
  subject: 'Signed building permit',
  message: 'The permit came back signed this morning - fire safety concept attached as requested.',
  files: [
    { name: 'Building permit (signed).pdf', bytes: 2.3 * MB, type: 'application/pdf' },
    { name: 'Fire safety concept.pdf', bytes: 5.1 * MB, type: 'application/pdf' },
  ],
  to: {},
  expiresInDays: 30,
}

/** Sophie's share, created AFTER approval is switched on, so it is held. */
export const HELD_SUBJECT = 'Facade samples - pricing'
const SOPHIE_HELD: DemoShare = {
  subject: HELD_SUBJECT,
  message: 'Martin, pricing for the three facade samples we discussed on site.',
  files: [{ name: 'Facade sample pricing.pdf', bytes: 0.9 * MB, type: 'application/pdf' }],
  to: { users: ['martin'] },
  expiresInDays: 7,
}

export interface Demo {
  heroShareId: string
  heroLinkToken: string
  heldShareId: string
}

async function ok(r: Response, what: string): Promise<Response> {
  if (!r.ok) throw new Error(`[docs seed] ${what} failed: ${r.status} ${await r.text()}`)
  return r
}

async function json<T>(r: Response, what: string): Promise<T> {
  return (await (await ok(r, what)).json()) as T
}

/** A file whose first bytes match its extension, so the backend records the
 * same type a real upload would get; the rest is filler. */
export function fileBytes(f: DemoFile): Uint8Array {
  const buf = new Uint8Array(Math.round(f.bytes))
  const magic = f.name.endsWith('.pdf')
    ? '%PDF-1.7\n'
    : f.name.endsWith('.zip') || f.name.endsWith('.docx') || f.name.endsWith('.xlsx')
      ? 'PK\x03\x04'
      : ''
  for (let i = 0; i < magic.length; i++) buf[i] = magic.charCodeAt(i)
  return buf
}

async function ensureUser(admin: string, p: Persona): Promise<number> {
  const list = await json<{ items: Array<{ id: number; email: string }> }>(
    await apiFetch(admin, `/api/admin/users?q=${encodeURIComponent(p.email)}`),
    `look up ${p.email}`,
  )
  const found = list.items.find((u) => u.email === p.email)
  if (found) return found.id
  const created = await json<{ id: number }>(
    await apiFetch(admin, '/api/admin/users', {
      method: 'POST',
      body: JSON.stringify({
        email: p.email,
        display_name: p.displayName,
        password: PASSWORD,
        target_role: p.role,
      }),
    }),
    `create ${p.email}`,
  )
  return created.id
}

async function ensureGroup(
  admin: string,
  name: string,
  description: string,
  memberIds: number[],
): Promise<number> {
  const list = await json<{ items: Array<{ id: number; name: string }> }>(
    await apiFetch(admin, '/api/groups'),
    'list groups',
  )
  let id = list.items.find((g) => g.name === name)?.id
  if (id === undefined) {
    id = (
      await json<{ id: number }>(
        await apiFetch(admin, '/api/groups', {
          method: 'POST',
          body: JSON.stringify({ name, description }),
        }),
        `create group ${name}`,
      )
    ).id
  }
  // Adding a member twice is harmless; the endpoint answers with the group.
  await ok(
    await apiFetch(admin, `/api/groups/${id}/members`, {
      method: 'POST',
      body: JSON.stringify({ user_ids: memberIds }),
    }),
    `add members to ${name}`,
  )
  return id
}

async function findShare(token: string, box: 'outbox' | 'inbox', subject: string): Promise<string | null> {
  const list = await json<{ items: Array<{ id: string; subject: string | null }> }>(
    await apiFetch(token, `/api/shares?box=${box}&q=${encodeURIComponent(subject)}`),
    `find share ${subject}`,
  )
  return list.items.find((s) => s.subject === subject)?.id ?? null
}

async function createShare(
  token: string,
  s: DemoShare,
  ids: { users: Record<string, number>; groups: Record<string, number> },
  kind: 'outbound' | 'inbound',
): Promise<string> {
  const existing = await findShare(token, 'outbox', s.subject)
  if (existing) return existing

  const expiresAt =
    s.expiresInDays === null ? null : new Date(Date.now() + s.expiresInDays * 86_400_000).toISOString()
  const created = await json<{ id: string }>(
    await apiFetch(token, '/api/shares', {
      method: 'POST',
      body: JSON.stringify({
        kind,
        subject: s.subject,
        message: s.message ?? null,
        recipients: {
          user_ids: (s.to.users ?? []).map((k) => ids.users[k]),
          group_ids: (s.to.groups ?? []).map((g) => ids.groups[g]),
        },
        expires_at: expiresAt,
        public_link: s.publicLink ?? null,
      }),
    }),
    `create share ${s.subject}`,
  )
  for (const f of s.files) {
    const form = new FormData()
    form.append('share_id', created.id)
    form.append('file', new Blob([fileBytes(f)], { type: f.type }), f.name)
    await ok(
      await fetch(`${BASE}/api/uploads/direct`, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}` },
        body: form,
      }),
      `upload ${f.name}`,
    )
  }
  if (s.end === 'expire') {
    await ok(await apiFetch(token, `/api/shares/${created.id}/expire`, { method: 'POST' }), 'expire')
  } else if (s.end === 'revoke') {
    await ok(await apiFetch(token, `/api/shares/${created.id}`, { method: 'DELETE' }), 'revoke')
  }
  return created.id
}

export async function seedDemo(): Promise<Demo> {
  const admin = await apiLogin(ADMIN_EMAIL, ADMIN_PASSWORD)

  await ok(
    await apiFetch(admin, '/api/account/display-name', {
      method: 'PATCH',
      body: JSON.stringify({ display_name: 'Anna Berger' }),
    }),
    'rename the admin',
  )
  // Link URLs on the share page read the site URL, so they look like a real
  // instance's instead of localhost.
  await ok(
    await apiFetch(admin, '/api/admin/settings/site', {
      method: 'PUT',
      body: JSON.stringify({
        site_url: 'https://files.heron-demo.example',
        site_timezone: 'Europe/Vienna',
      }),
    }),
    'site settings',
  )
  // In the render check the journey specs ran first, and forced-2fa leaves
  // employees required to enrol - which would stop Lukas at /account/2fa.
  await ok(
    await apiFetch(admin, '/api/admin/settings/twofa', {
      method: 'PUT',
      body: JSON.stringify({ required_roles: [], required_group_ids: [] }),
    }),
    '2FA policy',
  )

  const users: Record<string, number> = {}
  for (const [key, p] of Object.entries(PEOPLE)) users[key] = await ensureUser(admin, p)

  const groups: Record<string, number> = {
    [RIVERSIDE]: await ensureGroup(
      admin,
      RIVERSIDE,
      'Everyone working on the Riverside residential project.',
      [users.lukas, users.sophie, users.martin],
    ),
    [NOVAK]: await ensureGroup(admin, NOVAK, 'Due diligence with Novak Legal.', [
      users.lukas,
      users.julia,
    ]),
  }
  const ids = { users, groups }

  const lukas = await apiLogin(PEOPLE.lukas.email, PASSWORD)
  // The German shot switches Lukas to `de`; a re-run starts from English.
  await ok(
    await apiFetch(lukas, '/api/account/locale', {
      method: 'PATCH',
      body: JSON.stringify({ locale: 'en' }),
    }),
    'reset locale',
  )
  let heroShareId = ''
  for (const s of LUKAS_SHARES) {
    const id = await createShare(lukas, s, ids, 'outbound')
    if (s.subject === HERO_SUBJECT) heroShareId = id
  }

  const martin = await apiLogin(PEOPLE.martin.email, PASSWORD)
  await createShare(martin, MARTIN_INBOUND, ids, 'inbound')

  // Approval goes on last, so only Sophie's share is held.
  await ok(
    await apiFetch(admin, '/api/admin/settings/share-approval', {
      method: 'PUT',
      body: JSON.stringify({
        enabled: true,
        approver_mode: 'admins_only',
        scope: 'outbound',
        exempt_approvers: true,
        allow_content_review: true,
      }),
    }),
    'enable share approval',
  )
  const sophie = await apiLogin(PEOPLE.sophie.email, PASSWORD)
  const heldShareId = await createShare(sophie, SOPHIE_HELD, ids, 'outbound')

  const link = await json<{ url: string | null }>(
    await apiFetch(lukas, `/api/shares/${heroShareId}/public-link`),
    'read the hero link',
  )
  if (!link.url) throw new Error('[docs seed] the hero share has no re-viewable link URL')
  const heroLinkToken = link.url.split('/d/')[1]

  return { heroShareId, heroLinkToken, heldShareId }
}
