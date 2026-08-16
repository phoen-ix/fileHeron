/* Blocked sources page.
 *
 * The empty-state cases are the reason this file exists. The page defaults its
 * status filter to "in force", which is correct for a page about enforcement -
 * but on an instance whose blocks have all expired or been released it rendered
 * a bare "No blocks match these filters" over a table that did have history in
 * it. That reads as data loss, and it was hit within seconds of the page first
 * being opened on a real instance.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'

import en from '@/i18n/locales/en.json'

const LIVE_BLOCK = {
  id: 1,
  subject: '203.0.113.7',
  network: '203.0.113.7/32',
  is_network: false,
  reason: 'probe_path',
  source: 'auto',
  hit_count: 12,
  strikes: 1,
  last_path: '/wp-login.php',
  created_at: '2026-06-01T10:00:00',
  // Must be in the future, or `isLive()` is false and the row actions never
  // render.
  expires_at: '2099-01-01T00:00:00',
  released_at: null,
  released_by_id: null,
  note: null,
}

const ALLOWLIST = { entries: ['198.51.100.0/24'], invalid: [] as string[] }
const WATCHLIST = {
  available: true,
  enabled: true,
  window_sec: 3600,
  threshold: 3,
  auth_threshold: 15,
  items: [
    {
      ip: '192.0.2.9',
      offences: 2,
      last_signal: 'auth_failure',
      last_path: '/api/auth/login',
      last_seen: '2026-06-01T09:00:00',
    },
  ],
}

const empty = { items: [], total: 0, page: 1, page_size: 50 }

const listIpBlocks = vi.fn(async (_p?: unknown) => ({
  data: { items: [LIVE_BLOCK], total: 1, page: 1, page_size: 50 },
}))
const getScanGuardAllowlist = vi.fn(async () => ({ data: ALLOWLIST }))
const getScanGuardWatchlist = vi.fn(async () => ({ data: WATCHLIST }))
const createIpBlock = vi.fn(async (_p: unknown) => ({ data: LIVE_BLOCK }))
const releaseIpBlock = vi.fn(async (_id: number) => ({ data: {} }))
const releaseAllIpBlocks = vi.fn(async () => ({ data: { released: 1 } }))
const allowIpBlock = vi.fn(async (_id: number) => ({
  data: { block: LIVE_BLOCK, allowlist: ALLOWLIST.entries },
}))
const addScanGuardAllowlistEntry = vi.fn(async (_e: string) => ({ data: ALLOWLIST }))
const removeScanGuardAllowlistEntry = vi.fn(async (_e: string) => ({
  data: { entries: [], invalid: [] },
}))

vi.mock('@/api/admin', () => ({
  listIpBlocks: (p: unknown) => listIpBlocks(p),
  createIpBlock: (p: unknown) => createIpBlock(p),
  releaseIpBlock: (id: number) => releaseIpBlock(id),
  releaseAllIpBlocks: () => releaseAllIpBlocks(),
  allowIpBlock: (id: number) => allowIpBlock(id),
  getScanGuardAllowlist: () => getScanGuardAllowlist(),
  addScanGuardAllowlistEntry: (e: string) => addScanGuardAllowlistEntry(e),
  removeScanGuardAllowlistEntry: (e: string) => removeScanGuardAllowlistEntry(e),
  getScanGuardWatchlist: () => getScanGuardWatchlist(),
}))

const pushToast = vi.fn()
const confirm = vi.fn(async () => true)
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast, confirm }) }))

import AdminIpBlocks from '@/views/AdminIpBlocks.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    fallbackLocale: 'en',
    messages: { en },
  })
  return mount(AdminIpBlocks, { global: { plugins: [i18n], stubs: { Pager: true } } })
}

/** Empty under the current filter, `historyCount` rows once status is lifted. */
function emptyWithHistory(historyCount: number) {
  listIpBlocks.mockImplementation(async (p: any) =>
    p?.status === 'all'
      ? { data: { ...empty, total: historyCount } }
      : { data: empty },
  )
}

describe('AdminIpBlocks', () => {
  beforeEach(() => {
    listIpBlocks.mockReset()
    listIpBlocks.mockImplementation(async () => ({
      data: { items: [LIVE_BLOCK], total: 1, page: 1, page_size: 50 },
    }))
    getScanGuardAllowlist.mockClear()
    getScanGuardWatchlist.mockClear()
    pushToast.mockClear()
    confirm.mockClear()
  })

  it('loads the blocks, the watchlist and the allowlist on mount', async () => {
    const w = makeWrapper()
    await flushPromises()

    expect(listIpBlocks).toHaveBeenCalled()
    expect(getScanGuardAllowlist).toHaveBeenCalled()
    expect(getScanGuardWatchlist).toHaveBeenCalled()

    expect(w.text()).toContain('203.0.113.7') // the block row
    expect(w.text()).toContain('Scanner bait') // reason.probe_path
    expect(w.text()).toContain('Automatic') // source.auto
    expect(w.text()).toContain('192.0.2.9') // watchlist row
    expect(w.text()).toContain('198.51.100.0/24') // allowlist entry
    expect(w.text()).toContain('Release all') // a live block exists
  })

  it('offers a way to the history when the default filter hides everything', async () => {
    emptyWithHistory(2)
    const w = makeWrapper()
    await flushPromises()

    // Not the bare dead end it used to be.
    expect(w.text()).toContain('Nothing is being refused right now.')
    expect(w.text()).toContain('2 hidden by this filter.')
    expect(w.text()).toContain('Show all')
    // The count is asked for with the status lifted, not with a hardcoded shape.
    expect(listIpBlocks).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'all', page_size: 1 }),
    )
  })

  it('the way out actually works', async () => {
    emptyWithHistory(2)
    const w = makeWrapper()
    await flushPromises()

    listIpBlocks.mockClear()
    const showAll = w.findAll('button').find((b) => b.text() === 'Show all')
    expect(showAll, 'the Show all control should be rendered').toBeTruthy()
    await showAll!.trigger('click')
    await flushPromises()

    expect(listIpBlocks).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'all', page_size: 50 }),
    )
  })

  it('says nothing about history when there is none', async () => {
    // Empty under every status - a genuinely fresh instance.
    listIpBlocks.mockImplementation(async () => ({ data: empty }))
    const w = makeWrapper()
    await flushPromises()

    expect(w.text()).toContain('Nothing is being refused right now.')
    expect(w.text()).not.toContain('hidden by this filter')
    expect(w.findAll('button').some((b) => b.text() === 'Show all')).toBe(false)
  })

  it('a failed history count leaves an empty list empty, not broken', async () => {
    // `usePaginatedList` turns anything the fetcher throws into an error box, so
    // the supplementary count must never be able to do that.
    listIpBlocks.mockImplementation(async (p: any) => {
      if (p?.status === 'all') throw new Error('boom')
      return { data: empty }
    })
    const w = makeWrapper()
    await flushPromises()

    expect(w.text()).toContain('Nothing is being refused right now.')
    expect(w.find('[role="alert"]').exists()).toBe(false)
  })

  it('changing the status filter refetches with it', async () => {
    const w = makeWrapper()
    await flushPromises()

    listIpBlocks.mockClear()
    // [0] is the manual-block duration picker; the filters follow it.
    await w.findAll('select')[1].setValue('released')
    await flushPromises()

    expect(listIpBlocks).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'released' }),
    )
  })
})
