/* Adding a member reloaded the group through the page's `loading` flag, whose
 * `v-if` replaces the whole page with "Loading...": the search field was
 * destroyed and rebuilt on every add, remove and save, so the focus and the
 * scroll position were lost each time. A second member took a fresh click
 * into the field (found 2026-11-21 while looking for the recipient picker's
 * "second time" defect elsewhere). */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

import { click, type } from '../helpers/user'

const api = vi.hoisted(() => ({
  getGroup: vi.fn(),
  addMembers: vi.fn(),
  removeMember: vi.fn(),
  updateGroup: vi.fn(),
  deleteGroup: vi.fn(),
  searchUsers: vi.fn(),
}))
vi.mock('@/api/groups', () => ({
  getGroup: (id: number) => api.getGroup(id),
  addMembers: (id: number, ids: number[]) => api.addMembers(id, ids),
  removeMember: (id: number, uid: number) => api.removeMember(id, uid),
  updateGroup: (id: number, body: unknown) => api.updateGroup(id, body),
  deleteGroup: (id: number) => api.deleteGroup(id),
}))
vi.mock('@/api/users', () => ({ searchUsers: (q: string) => api.searchUsers(q) }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '3' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

import AdminGroupDetail from '@/views/AdminGroupDetail.vue'

const ANNA = { user_id: 7, display_name: 'Anna Client', email: 'anna@client.test', role: 'client' }
const BEN = { user_id: 8, display_name: 'Ben Client', email: 'ben@client.test', role: 'client' }

let members: (typeof ANNA)[] = []
const group = () => ({
  id: 3,
  name: 'Design',
  description: null,
  is_company_inbox: false,
  member_count: members.length,
  members: members.map((m) => ({ ...m, joined_at: '2026-11-01T10:00:00' })),
})

const mounted: { unmount: () => void }[] = []

async function mountPage() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(AdminGroupDetail, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: { AdminPageHeader: { template: '<header><slot name="title" /></header>' } },
    },
  })
  mounted.push(w)
  await flushPromises()
  return w
}

async function settle() {
  vi.advanceTimersByTime(250)
  await flushPromises()
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  members = []
  Object.values(api).forEach((f) => f.mockReset())
  api.getGroup.mockImplementation(async () => ({ data: group() }))
  api.addMembers.mockImplementation(async (_id: number, ids: number[]) => {
    members.push(...[ANNA, BEN].filter((u) => ids.includes(u.user_id)))
    return { data: {} }
  })
  api.searchUsers.mockImplementation(async (q: string) => ({
    data: { items: [ANNA, BEN].filter((u) => u.email.includes(q.toLowerCase())) },
  }))
})

afterEach(() => {
  mounted.splice(0).forEach((w) => w.unmount())
  vi.useRealTimers()
})

describe('AdminGroupDetail member add', () => {
  const addButton = (w: Awaited<ReturnType<typeof mountPage>>, name: string) =>
    w
      .findAll('.add-result')
      .find((r) => r.text().includes(name))!
      .find('button')

  it('adds two members in a row; the search field stays where it is', async () => {
    const w = await mountPage()
    const input = w.find('.add-member input').element

    await type(input, 'anna')
    await settle()
    await click(addButton(w, 'Anna Client'))
    await flushPromises()

    // The same element, still in the page: the reload did not rebuild it.
    expect(document.body.contains(input)).toBe(true)
    expect(w.find('.add-member input').element).toBe(input)
    expect(w.find('.member-list').text()).toContain('Anna Client')

    await type(input, 'ben')
    await settle()
    await click(addButton(w, 'Ben Client'))
    await flushPromises()

    expect(api.addMembers.mock.calls).toEqual([
      [3, [ANNA.user_id]],
      [3, [BEN.user_id]],
    ])
    expect(w.findAll('.member-row')).toHaveLength(2)
  })

  it('drops a search answer that arrives after a member was added', async () => {
    const w = await mountPage()
    const input = w.find('.add-member input').element
    await type(input, 'anna')
    await settle()

    // A second search is in flight when the user adds from the first list.
    let release!: (v: unknown) => void
    api.searchUsers.mockImplementation(() => new Promise((res) => (release = res)))
    await type(input, 'ann')
    vi.advanceTimersByTime(250)
    await click(addButton(w, 'Anna Client'))
    await flushPromises()
    release({ data: { items: [ANNA, BEN] } })
    await flushPromises()

    expect(w.findAll('.add-result')).toHaveLength(0)
  })
})
