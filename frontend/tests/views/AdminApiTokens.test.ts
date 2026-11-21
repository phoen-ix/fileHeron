/* The owner picker on "create a token for a user". A search answer still in
 * flight when the admin picked refilled the suggestion list the pick had just
 * emptied, and nothing kept a slower answer for an earlier query from
 * replacing a later one (found 2026-11-21, the recipient picker's "second
 * time" shape). */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

import { click, type } from '../helpers/user'

const api = vi.hoisted(() => ({ searchUsers: vi.fn() }))
vi.mock('@/api/users', () => ({ searchUsers: (q: string) => api.searchUsers(q) }))
vi.mock('@/api/admin', () => ({
  adminListApiTokens: vi.fn(async () => ({ data: { items: [], total: 0 } })),
  adminCreateApiToken: vi.fn(),
  adminDisableApiToken: vi.fn(),
  adminReactivateApiToken: vi.fn(),
  adminRevokeApiToken: vi.fn(),
}))

import AdminApiTokens from '@/views/AdminApiTokens.vue'

const ALICE = { user_id: 1, display_name: 'Alice', email: 'alice@x.test', role: 'employee' }
const BOB = { user_id: 2, display_name: 'Bob', email: 'bob@x.test', role: 'employee' }

const mounted: { unmount: () => void }[] = []

async function mountPage() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(AdminApiTokens, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: { ExpiryPicker: true, Pager: true, StepUpDialog: true },
    },
  })
  mounted.push(w)
  await flushPromises()
  await click(w.find('.fh-tab-toolbar button'))
  return w
}

async function settle() {
  vi.advanceTimersByTime(250)
  await flushPromises()
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  api.searchUsers.mockReset()
  api.searchUsers.mockImplementation(async (q: string) => ({
    data: { items: [ALICE, BOB].filter((u) => u.email.startsWith(q.toLowerCase())) },
  }))
})

afterEach(() => {
  mounted.splice(0).forEach((w) => w.unmount())
  vi.useRealTimers()
})

describe('AdminApiTokens owner picker', () => {
  const owner = (w: Awaited<ReturnType<typeof mountPage>>) => w.find('.picked-user')

  it('picks an owner, then another, without leaving the field', async () => {
    const w = await mountPage()
    const input = w.find('.create-form input[type="search"]').element

    await type(input, 'al')
    await settle()
    await click(w.find('.user-suggest'))
    expect(owner(w).text()).toContain('alice@x.test')
    expect(w.findAll('.user-suggest')).toHaveLength(0)

    await type(input, 'bo')
    expect(owner(w).exists()).toBe(false)
    await settle()
    await click(w.find('.user-suggest'))
    expect(owner(w).text()).toContain('bob@x.test')
  })

  it('drops a search answer that arrives after the pick', async () => {
    const w = await mountPage()
    const input = w.find('.create-form input[type="search"]').element
    await type(input, 'al')
    await settle()

    let release!: (v: unknown) => void
    api.searchUsers.mockImplementation(() => new Promise((res) => (release = res)))
    await type(input, 'ali')
    vi.advanceTimersByTime(250)
    await click(w.find('.user-suggest'))
    release({ data: { items: [ALICE, BOB] } })
    await flushPromises()

    expect(w.findAll('.user-suggest')).toHaveLength(0)
    expect(owner(w).text()).toContain('alice@x.test')
  })

  it('keeps the answer to the newest query', async () => {
    const w = await mountPage()
    const input = w.find('.create-form input[type="search"]').element
    const waiting: Array<(v: unknown) => void> = []
    api.searchUsers.mockImplementation(() => new Promise((res) => waiting.push(res)))

    await type(input, 'al')
    vi.advanceTimersByTime(250)
    await type(input, 'bo')
    vi.advanceTimersByTime(250)
    waiting[1]({ data: { items: [BOB] } })
    await flushPromises()
    waiting[0]({ data: { items: [ALICE] } })
    await flushPromises()

    expect(w.findAll('.user-suggest').map((b) => b.find('.row-name').text())).toEqual(['Bob'])
  })
})
