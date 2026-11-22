/* components/admin/PolicyGate.vue: the "who may" block the policy pages
 * share. The pages' own tests (PolicyPages.test.ts) pin the rendered markup;
 * this pins the component's contract - it emits the whole model on every
 * change, hides the allowlist only under "everyone", and shows the inbox pill
 * only when the page passes a label for it. */
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const CY = { user_id: 9, display_name: 'Cy', email: 'cy@test.local', role: 'employee' }
const DEE = { user_id: 10, display_name: 'Dee', email: 'dee@test.local', role: 'employee' }
const api = vi.hoisted(() => ({ searchUsers: vi.fn() }))
vi.mock('@/api/users', () => ({ searchUsers: (q: string) => api.searchUsers(q) }))

import PolicyGate from '@/components/admin/PolicyGate.vue'

import { click, type } from '../helpers/user'

beforeEach(() => {
  api.searchUsers.mockReset()
  api.searchUsers.mockImplementation(async (q: string) => ({
    data: { items: [CY, DEE].filter((u) => u.email.startsWith(q.toLowerCase())) },
  }))
})

const LABELS = {
  mode: 'Mode',
  heading: 'Allowlist',
  help: 'Always allowed',
  users: 'Users',
  usersPlaceholder: 'Search',
  groups: 'Groups',
}
const MODES = [
  { value: 'everyone', label: 'Everyone', help: 'all' },
  { value: 'admins_only', label: 'Admins', help: 'only admins' },
]
const GROUPS = [
  { id: 1, name: 'design', is_company_inbox: false },
  { id: 2, name: 'inbox', is_company_inbox: true },
]

function mountGate(mode: string, labels: Record<string, string> = LABELS) {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(PolicyGate, {
    props: {
      modelValue: { mode, users: [], groups: [] },
      modes: MODES,
      labels,
      groups: GROUPS,
    } as never,
    global: { plugins: [i18n] },
  })
}

describe('PolicyGate', () => {
  it('hides the allowlist only under "everyone"', () => {
    expect(mountGate('everyone').find('section.allowlist').exists()).toBe(false)
    expect(mountGate('admins_only').find('section.allowlist').exists()).toBe(true)
  })

  it('emits the whole model when the mode, a user or a group changes', async () => {
    const w = mountGate('admins_only')
    await w.find('input[value="everyone"]').setValue(true)
    expect(w.emitted('update:modelValue')?.[0]).toEqual([
      { mode: 'everyone', users: [], groups: [] },
    ])

    await w.find('input[type="search"]').setValue('cy')
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()
    await w.find('.user-suggest').trigger('click')
    expect(w.emitted('update:modelValue')?.[1]).toEqual([
      {
        mode: 'admins_only',
        users: [{ id: 9, display_name: 'Cy', email: 'cy@test.local', role: 'employee' }],
        groups: [],
      },
    ])

    await w.findAll('.group-check input')[1].setValue(true)
    expect(w.emitted('update:modelValue')?.[2]).toEqual([
      { mode: 'admins_only', users: [], groups: [{ id: 2, name: 'inbox' }] },
    ])
  })

  it('shows the inbox pill only when given a label for it', () => {
    expect(mountGate('admins_only').find('.group-check .fh-pill').exists()).toBe(false)
    const w = mountGate('admins_only', { ...LABELS, inbox: 'company inbox' })
    expect(w.find('.group-check .fh-pill').text()).toBe('company inbox')
  })
})

/* The same "second time" shape as the recipient picker (2026-11-20): every
 * test added one user. The gate is a controlled component, so the host feeds
 * each emitted model back in. */
describe('PolicyGate allowlist, several users', () => {
  const pause = () => new Promise((r) => setTimeout(r, 250))

  function mountControlled() {
    const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
    const w = mount(PolicyGate, {
      attachTo: document.body,
      props: {
        modelValue: { mode: 'admins_only', users: [], groups: [] },
        modes: MODES,
        labels: LABELS,
        groups: GROUPS,
        'onUpdate:modelValue': (v: unknown) => w.setProps({ modelValue: v } as never),
      } as never,
      global: { plugins: [i18n] },
    })
    return w
  }

  it('adds two users in a row without leaving the field', async () => {
    const w = mountControlled()
    const input = w.find('input[type="search"]').element
    await type(input, 'cy')
    await pause()
    await flushPromises()
    await click(w.find('.user-suggest'))

    await type(input, 'dee')
    await pause()
    await flushPromises()
    await click(w.find('.user-suggest'))

    const last = w.emitted('update:modelValue')?.at(-1)?.[0] as { users: { id: number }[] }
    expect(last.users.map((u) => u.id)).toEqual([CY.user_id, DEE.user_id])
    w.unmount()
  })

  it('drops a search answer that arrives after a user was added', async () => {
    const w = mountControlled()
    const input = w.find('input[type="search"]').element
    await type(input, 'cy')
    await pause()
    await flushPromises()

    let release!: (v: unknown) => void
    api.searchUsers.mockImplementation(() => new Promise((res) => (release = res)))
    await type(input, 'cy@')
    await pause()
    await click(w.find('.user-suggest'))
    release({ data: { items: [CY, DEE] } })
    await flushPromises()

    expect(w.findAll('.user-suggest')).toHaveLength(0)
    w.unmount()
  })
})

// Share approval is staff only: a client on the approver list never approves
// (services/share_approval.can_approve) and the settings refuse to store one,
// so the approval page must not offer clients - and must say why an already
// listed one blocks the save. The other policy pages may list a client.
describe('PolicyGate staffOnly', () => {
  const CLIENT = { user_id: 11, display_name: 'Cleo', email: 'cleo@test.local', role: 'client' }

  function mountWith(props: Record<string, unknown>) {
    api.searchUsers.mockImplementation(async () => ({ data: { items: [CY, CLIENT] } }))
    const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
    return mount(PolicyGate, {
      props: {
        modelValue: { mode: 'admins_only', users: [], groups: [] },
        modes: MODES,
        labels: { ...LABELS, notStaff: 'A client cannot approve' },
        groups: GROUPS,
        ...props,
      } as never,
      global: { plugins: [i18n] },
    })
  }

  async function offered(w: ReturnType<typeof mountWith>) {
    await w.find('input[type="search"]').setValue('c')
    await w.find('input[type="search"]').setValue('cl')
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()
    return w.findAll('.user-suggest').map((b) => b.find('.row-name').text())
  }

  it('offers no client for the list, and still offers one where the page does not ask', async () => {
    expect(await offered(mountWith({ staffOnly: true }))).toEqual(['Cy'])
    expect(await offered(mountWith({}))).toEqual(['Cy', 'Cleo'])
  })

  it('marks a client who is already on the list', () => {
    const listed = [
      { id: 9, display_name: 'Cy', email: 'cy@test.local', role: 'employee' },
      { id: 11, display_name: 'Cleo', email: 'cleo@test.local', role: 'client' },
    ]
    const w = mountWith({
      staffOnly: true,
      modelValue: { mode: 'admins_only', users: listed, groups: [] },
    })
    const rows = w.findAll('.picked-row')
    expect(rows[0].find('[data-testid="not-staff"]').exists()).toBe(false)
    expect(rows[1].get('[data-testid="not-staff"]').text()).toBe('A client cannot approve')

    const plain = mountWith({ modelValue: { mode: 'admins_only', users: listed, groups: [] } })
    expect(plain.find('[data-testid="not-staff"]').exists()).toBe(false)
  })
})
