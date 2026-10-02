import { spawnSync } from 'node:child_process'
import { createHash, randomBytes } from 'node:crypto'
import { crc32 } from 'node:zlib'

import { expect, test } from '@playwright/test'

import { ADMIN, BASE, USER, apiFetch, apiLogin } from '../helpers'

/* Journey: encryption at rest. The admin turns it on through the page (the
 * key-custody acknowledgement, then the password prompt); a new upload is
 * encrypted before it is released, and every way of reading it - a plain
 * download, the client's `bytes=1-1` probe, a range across the 1 MiB chunk
 * boundary, If-Range, the bulk ZIP - returns the original bytes. Turned off
 * again, a new upload stays plaintext and both kinds of row download side by
 * side. The stored bytes are checked in the backend container itself. */

const BACKEND_CONTAINER = process.env.E2E_BACKEND_CONTAINER ?? 'fileheron_e2e-backend'
const MiB = 1024 * 1024

function sha256(b: Uint8Array): string {
  return createHash('sha256').update(b).digest('hex')
}

/** `enc_version` and the first six stored bytes of a file row, read inside the
 * backend container - the API never says where or how a file is stored. */
function storedForm(fileId: string): { enc: string; head: string } {
  const py = [
    'import sys',
    'from app.database import SessionLocal',
    'from app.models.file import File',
    'from app.services.storage_backend import get_storage_backend',
    'db = SessionLocal()',
    'f = db.get(File, sys.argv[1])',
    'with get_storage_backend().open(f.storage_path) as fh:',
    '    head = fh.read(6)',
    'print(f.enc_version, head.hex())',
  ].join('\n')
  const r = spawnSync('docker', ['exec', BACKEND_CONTAINER, 'python', '-c', py, fileId], {
    encoding: 'utf8',
  })
  if (r.status !== 0) throw new Error(`[e2e] storedForm ${fileId}: ${r.stderr}`)
  const [enc, head] = r.stdout.trim().split(' ')
  return { enc, head }
}

async function newShare(token: string, label: string): Promise<string> {
  const search = await apiFetch(token, `/api/users/search?q=${encodeURIComponent(USER.email)}`)
  const items = (await search.json()).items as Array<{ user_id: number; email: string }>
  const recipientId = items.find((u) => u.email === USER.email)?.user_id
  expect(recipientId).toBeTruthy()
  const r = await apiFetch(token, '/api/shares', {
    method: 'POST',
    body: JSON.stringify({
      kind: 'outbound',
      recipients: { user_ids: [recipientId], group_ids: [] },
      expires_at: null,
      subject: `E2E encryption ${label} ${Date.now()}`,
    }),
  })
  expect(r.status).toBe(201)
  return (await r.json()).id as string
}

async function upload(token: string, shareId: string, name: string, data: Uint8Array): Promise<string> {
  const form = new FormData()
  form.append('share_id', shareId)
  form.append('file', new Blob([data], { type: 'application/octet-stream' }), name)
  const r = await fetch(`${BASE}/api/uploads/direct`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}` },
    body: form,
  })
  if (!r.ok) throw new Error(`[e2e] upload ${name}: ${r.status} ${await r.text()}`)
  return (await r.json()).file_id as string
}

async function waitClean(token: string, shareId: string, count: number): Promise<void> {
  await expect
    .poll(
      async () => {
        const r = await apiFetch(token, `/api/shares/${shareId}`)
        const files = (await r.json()).files as Array<{ state: string }>
        return files.filter((f) => f.state === 'clean').length
      },
      { timeout: 60_000 },
    )
    .toBe(count)
}

async function signed(token: string, path: string): Promise<string> {
  const r = await apiFetch(token, path)
  expect(r.ok).toBeTruthy()
  return `${BASE}${(await r.json()).url as string}`
}

async function bytes(r: Response): Promise<Uint8Array> {
  return new Uint8Array(await r.arrayBuffer())
}

/** Each member's sha256 and stored CRC, read by Python's zipfile inside the
 * backend container - after `testzip()` has checked every CRC against the
 * member bytes. (A hand-rolled reader would have to follow Zip64 too.) */
function zipMembers(zip: Uint8Array): Map<string, { sha256: string; crc: number }> {
  const py = [
    'import hashlib, io, json, sys, zipfile',
    'z = zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read()))',
    'bad = z.testzip()',
    'assert bad is None, f"CRC mismatch in {bad}"',
    'print(json.dumps({i.filename: [hashlib.sha256(z.read(i)).hexdigest(), i.CRC] for i in z.infolist()}))',
  ].join('\n')
  const r = spawnSync('docker', ['exec', '-i', BACKEND_CONTAINER, 'python', '-c', py], {
    input: zip,
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  })
  if (r.status !== 0) throw new Error(`[e2e] zip check: ${r.stderr}`)
  const parsed = JSON.parse(r.stdout) as Record<string, [string, number]>
  return new Map(Object.entries(parsed).map(([k, [sha, crc]]) => [k, { sha256: sha, crc }]))
}

test.describe.serial('encryption at rest', () => {
  let admin = ''
  const big = new Uint8Array(randomBytes(2 * MiB + 12_345))
  const empty = new Uint8Array(0)
  let shareId = ''
  let bigId = ''

  test.beforeAll(async () => {
    admin = await apiLogin(ADMIN.email, ADMIN.password)
    // Encryption pauses - and falls back to storing plaintext - when a rewrite
    // would leave less than the low-storage floor (10 GiB by default) free, the
    // same line below which uploads are refused. A CI runner or a tmpfs clone
    // can sit under it, so the journey must not depend on the host's disk.
    const r = await apiFetch(admin, '/api/admin/settings/advanced', {
      method: 'PUT',
      body: JSON.stringify({ updates: { 'storage.low_threshold_bytes': MiB } }),
    })
    expect(r.ok).toBeTruthy()
  })

  test.afterAll(async () => {
    // Leave the stack as found: off, and the floor back at its default (the
    // docs tour and the other journeys run after).
    await apiFetch(admin, '/api/admin/settings/encryption', {
      method: 'PUT',
      body: JSON.stringify({ enabled: false, password: ADMIN.password }),
    })
    await apiFetch(admin, '/api/admin/settings/advanced', {
      method: 'PUT',
      body: JSON.stringify({ updates: { 'storage.low_threshold_bytes': null } }),
    })
  })

  test('the admin turns it on: acknowledgement first, then the password', async ({ page }) => {
    await page.goto('/login')
    await page.fill('#login-email', ADMIN.email)
    await page.fill('#login-password', ADMIN.password)
    await page.click('button[type=submit]')
    await expect(page).not.toHaveURL(/\/login(\?|$)/)

    await page.goto('/admin/settings/encryption')
    await expect(page.getByTestId('encryption-state')).toHaveText(/off/i)
    const turnOn = page.getByTestId('encryption-turn-on')
    await expect(turnOn).toBeDisabled()
    await page.getByTestId('encryption-ack').check()
    await turnOn.click()
    await page.getByTestId('step-up-password').fill(ADMIN.password)
    await page.getByTestId('step-up-confirm').click()
    await expect(page.getByTestId('step-up-dialog')).toHaveCount(0)
    await expect(page.getByTestId('encryption-state')).toHaveText(/on/i)
  })

  test('a new upload is encrypted before release and reads back byte for byte', async () => {
    shareId = await newShare(admin, 'on')
    bigId = await upload(admin, shareId, 'big.bin', big)
    const emptyId = await upload(admin, shareId, 'empty.bin', empty)
    await waitClean(admin, shareId, 2)

    for (const id of [bigId, emptyId]) {
      const stored = storedForm(id)
      expect(stored.enc).toBe('1')
      expect(stored.head).toBe(Buffer.from('FHEnc\0').toString('hex'))
    }

    const full = await fetch(await signed(admin, `/api/files/${bigId}/download-url`))
    expect(full.status).toBe(200)
    expect(Number(full.headers.get('content-length'))).toBe(big.length)
    expect(sha256(await bytes(full))).toBe(sha256(big))

    const none = await fetch(await signed(admin, `/api/files/${emptyId}/download-url`))
    expect(none.status).toBe(200)
    expect((await bytes(none)).length).toBe(0)
  })

  test('ranges, the client probe and If-Range work on ciphertext', async () => {
    const url = await signed(admin, `/api/files/${bigId}/download-url`)

    const probe = await fetch(url, { headers: { range: 'bytes=1-1' } })
    expect(probe.status).toBe(206)
    expect(probe.headers.get('content-range')).toBe(`bytes 1-1/${big.length}`)
    expect(Array.from(await bytes(probe))).toEqual([big[1]])

    const a = MiB - 10
    const b = MiB + 25
    const across = await fetch(url, { headers: { range: `bytes=${a}-${b}` } })
    expect(across.status).toBe(206)
    const etag = across.headers.get('etag')
    expect(etag).toMatch(/^"fhe-/)
    expect(sha256(await bytes(across))).toBe(sha256(big.subarray(a, b + 1)))

    const resumed = await fetch(url, { headers: { range: `bytes=${MiB}-`, 'if-range': etag as string } })
    expect(resumed.status).toBe(206)
    expect(sha256(await bytes(resumed))).toBe(sha256(big.subarray(MiB)))

    const stale = await fetch(url, { headers: { range: `bytes=${MiB}-`, 'if-range': '"something-else"' } })
    expect(stale.status).toBe(200)
    expect(sha256(await bytes(stale))).toBe(sha256(big))
  })

  test('the bulk ZIP carries the plaintext members and their CRCs', async () => {
    const r = await fetch(await signed(admin, `/api/files/${shareId}/download-zip-url`))
    expect(r.status).toBe(200)
    const members = zipMembers(await bytes(r))
    for (const [name, data] of [
      ['big.bin', big],
      ['empty.bin', empty],
    ] as const) {
      const m = members.get(name)
      expect(m, name).toBeTruthy()
      expect(m!.sha256).toBe(sha256(data))
      expect(m!.crc).toBe(crc32(data))
    }
  })

  test('turned off, new uploads stay plaintext and both kinds download', async () => {
    const off = await apiFetch(admin, '/api/admin/settings/encryption', {
      method: 'PUT',
      body: JSON.stringify({ enabled: false, password: ADMIN.password }),
    })
    expect(off.ok).toBeTruthy()

    const plain = new Uint8Array(randomBytes(300_000))
    const plainId = await upload(admin, shareId, 'plain.bin', plain)
    await waitClean(admin, shareId, 3)
    expect(storedForm(plainId).enc).toBe('None')
    expect(storedForm(bigId).enc).toBe('1')

    for (const [id, data] of [
      [plainId, plain],
      [bigId, big],
    ] as const) {
      const r = await fetch(await signed(admin, `/api/files/${id}/download-url`))
      expect(r.status).toBe(200)
      expect(sha256(await bytes(r))).toBe(sha256(data))
    }
  })
})
