/* Audit log page.
 *
 * This file exists because the page silently dropped the only field that says
 * WHAT a system-originated event was about. `audit_log.ip` means "the actor's
 * address" and is filled from `request.client.host`, so it is NULL for every
 * event with no actor - an automatic scan-guard block, a failed cron, an
 * undeliverable email. The detail lives in `metadata_json`, the API has always
 * returned it as `extra`, and the CSV export has always included it. The table
 * rendered six columns and no metadata, so an operator looking at
 * `ip_blocked / ip_block:17` saw two dashes and no way to learn which address
 * had been blocked. 704 of 1,537 rows on the reference instance were in that
 * state.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'

import en from '@/i18n/locales/en.json'
import type { AdminAuditRow } from '@/types/api'

/** A real row, copied from the reference instance. */
const AUTO_BLOCK: AdminAuditRow = {
  id: 1524,
  event_type: 'ip_blocked',
  actor_user_id: null,
  actor_display_name: null,
  actor_email: null,
  target_type: 'ip_block',
  target_id: '17',
  request_id: null,
  ip: null,
  extra: {
    subject: '45.148.10.120',
    reason: 'probe_path',
    minutes: 60,
    strikes: 1,
    is_network: false,
    source: 'auto',
  },
  created_at: '2026-09-03T18:31:08',
}

/** An actor-originated row: has an IP, carries no metadata. */
const LOGIN: AdminAuditRow = {
  id: 1525,
  event_type: 'login_success',
  actor_user_id: 1,
  actor_display_name: 'Admin',
  actor_email: 'a***@example.com',
  target_type: 'user',
  target_id: '1',
  request_id: 'abcdef1234567890',
  ip: '203.0.113.7',
  extra: null,
  created_at: '2026-09-03T18:32:00',
}

const listAuditLog = vi.fn(async (_p?: unknown) => ({
  data: { items: [AUTO_BLOCK, LOGIN], total: 2, page: 1, page_size: 50 },
}))
const exportAuditCsv = vi.fn(async (_p?: unknown) => ({ data: new Blob() }))

vi.mock('@/api/admin', () => ({
  listAuditLog: (p: unknown) => listAuditLog(p),
  exportAuditCsv: (p: unknown) => exportAuditCsv(p),
}))

const pushToast = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast, confirm: vi.fn() }) }))

import AdminAuditLog from '@/views/AdminAuditLog.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    fallbackLocale: 'en',
    messages: { en },
  })
  return mount(AdminAuditLog, {
    global: { plugins: [i18n], stubs: { Pager: true, RouterLink: true } },
  })
}

describe('AdminAuditLog metadata', () => {
  beforeEach(() => {
    listAuditLog.mockClear()
    pushToast.mockClear()
  })

  it('a row carrying metadata offers a details toggle; one without does not', async () => {
    const w = makeWrapper()
    await flushPromises()
    // Exactly one of the two rows has `extra`.
    expect(w.findAll('button.disclose')).toHaveLength(1)
  })

  it('expanding an ip_blocked row reveals the blocked address', async () => {
    const w = makeWrapper()
    await flushPromises()

    // Collapsed: the address the operator actually needs is nowhere on the page.
    expect(w.text()).not.toContain('45.148.10.120')

    await w.get('button.disclose').trigger('click')
    await flushPromises()

    const text = w.text()
    expect(text).toContain('subject')
    expect(text).toContain('45.148.10.120')
    expect(text).toContain('probe_path')
  })

  it('renders falsey metadata values instead of blanking them', async () => {
    const w = makeWrapper()
    await flushPromises()
    await w.get('button.disclose').trigger('click')
    await flushPromises()
    // `is_network: false` must be visible - a plain string coercion would
    // render an empty cell and read as "no value recorded".
    expect(w.text()).toContain('false')
  })

  it('renders metadata as text, never as markup', async () => {
    listAuditLog.mockImplementation(async () => ({
      data: {
        items: [{ ...AUTO_BLOCK, extra: { note: '<img src=x onerror=alert(1)>' } }],
        total: 1,
        page: 1,
        page_size: 50,
      },
    }))
    const w = makeWrapper()
    await flushPromises()
    await w.get('button.disclose').trigger('click')
    await flushPromises()

    // Metadata is attacker-influenced: `subject` is a remote address and
    // redacted paths appear in other events.
    expect(w.find('dd img').exists()).toBe(false)
    expect(w.text()).toContain('<img src=x onerror=alert(1)>')
  })

  it('the toggle reports its state for assistive tech', async () => {
    const w = makeWrapper()
    await flushPromises()
    const btn = w.get('button.disclose')
    expect(btn.attributes('aria-expanded')).toBe('false')
    await btn.trigger('click')
    expect(w.get('button.disclose').attributes('aria-expanded')).toBe('true')
  })
})
