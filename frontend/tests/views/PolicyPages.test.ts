/* The three policy pages that share the "who may" mode + allowlist block:
 * API tokens, public links, share approval. Pinned BEFORE that block becomes
 * one component, so the refactor has to keep both the behaviour (pick, remove,
 * toggle, what a save sends) and the rendered markup - each form is snapshotted
 * with the scoped-CSS attributes stripped, so moving the block into a component
 * changes nothing the snapshot sees. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Component } from 'vue'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  getTokenPolicy: vi.fn(),
  updateTokenPolicy: vi.fn(),
  getPublicLinkPolicy: vi.fn(),
  updatePublicLinkPolicy: vi.fn(),
  getShareApprovalSettings: vi.fn(),
  updateShareApprovalSettings: vi.fn(),
  searchUsers: vi.fn(),
}))
vi.mock('@/api/admin', () => ({
  getTokenPolicy: () => api.getTokenPolicy(),
  updateTokenPolicy: (p: unknown) => api.updateTokenPolicy(p),
  getPublicLinkPolicy: () => api.getPublicLinkPolicy(),
  updatePublicLinkPolicy: (p: unknown) => api.updatePublicLinkPolicy(p),
  getShareApprovalSettings: () => api.getShareApprovalSettings(),
  updateShareApprovalSettings: (p: unknown) => api.updateShareApprovalSettings(p),
}))
vi.mock('@/api/groups', () => ({
  listGroups: async () => ({
    data: {
      items: [
        { id: 1, name: 'design', is_company_inbox: false },
        { id: 2, name: 'inbox', is_company_inbox: true },
      ],
    },
  }),
}))
vi.mock('@/api/users', () => ({ searchUsers: (q: string) => api.searchUsers(q) }))

import { useAuthStore } from '@/stores/auth'
import AdminSettingsApiTokens from '@/views/AdminSettingsApiTokens.vue'
import AdminSettingsPublicLinks from '@/views/AdminSettingsPublicLinks.vue'
import AdminSettingsShareApproval from '@/views/AdminSettingsShareApproval.vue'

const ADA = { id: 5, display_name: 'Ada', email: 'ada@test.local', role: 'employee' }
const BOB = { user_id: 6, display_name: 'Bob', email: 'bob@test.local', role: 'client' }

interface Page {
  name: string
  component: Component
  load: ReturnType<typeof vi.fn>
  save: ReturnType<typeof vi.fn>
  response: (mode: string) => Record<string, unknown>
  modeKey: string
  usersKey: string
  groupsKey: string
  allowlistAlways: boolean
}

const PAGES: Page[] = [
  {
    name: 'API tokens',
    component: AdminSettingsApiTokens,
    load: api.getTokenPolicy,
    save: api.updateTokenPolicy,
    response: (mode) => ({ mode, allowed_users: [ADA], allowed_groups: [{ id: 1, name: 'design' }] }),
    modeKey: 'mode',
    usersKey: 'allowed_user_ids',
    groupsKey: 'allowed_group_ids',
    allowlistAlways: false,
  },
  {
    name: 'public links',
    component: AdminSettingsPublicLinks,
    load: api.getPublicLinkPolicy,
    save: api.updatePublicLinkPolicy,
    response: (mode) => ({
      mode,
      allowed_users: [ADA],
      allowed_groups: [{ id: 1, name: 'design' }],
      external_recipients_enabled: false,
      external_recipients_offer_invite: false,
    }),
    modeKey: 'mode',
    usersKey: 'allowed_user_ids',
    groupsKey: 'allowed_group_ids',
    allowlistAlways: false,
  },
  {
    name: 'share approval',
    component: AdminSettingsShareApproval,
    load: api.getShareApprovalSettings,
    save: api.updateShareApprovalSettings,
    response: (mode) => ({
      enabled: true,
      approver_mode: mode,
      scope: 'outbound',
      exempt_approvers: false,
      allow_content_review: true,
      approver_users: [ADA],
      approver_groups: [{ id: 1, name: 'design' }],
    }),
    modeKey: 'approver_mode',
    usersKey: 'approver_user_ids',
    groupsKey: 'approver_group_ids',
    allowlistAlways: true,
  },
]

async function mountPage(page: Page, mode: string) {
  page.load.mockResolvedValue({ data: page.response(mode) })
  page.save.mockImplementation(async () => ({ data: page.response(mode) }))
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(page.component, {
    global: {
      plugins: [i18n],
      stubs: { AdminPageHeader: true, TunableFields: true, RouterLink: true },
    },
  })
  await flushPromises()
  return w
}

/** The form as rendered, minus what is not visible and changes when markup
 * moves into another component: scoped-CSS hash attributes, comments (template
 * comments and v-if anchors) and the pretty-printer's whitespace between tags.
 * Comments are removed as DOM nodes, not by a regex over the HTML - this runs
 * once, at the end of a test, so the mounted tree can lose them. */
function markup(w: Awaited<ReturnType<typeof mountPage>>): string {
  const form = w.find('form')
  const walker = document.createTreeWalker(form.element, NodeFilter.SHOW_COMMENT)
  const comments: Node[] = []
  while (walker.nextNode()) comments.push(walker.currentNode)
  for (const c of comments) c.parentNode?.removeChild(c)
  expect(comments.length, 'vacuity: the forms carry v-if anchors').toBeGreaterThan(0)
  return form
    .html()
    .replace(/ data-v-[0-9a-f]+=""/g, '')
    .replace(/>\s+</g, '><')
}

beforeEach(() => {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.refreshMe = vi.fn(async () => undefined) as unknown as typeof auth.refreshMe
  for (const fn of Object.values(api)) fn.mockReset()
  api.searchUsers.mockResolvedValue({ data: { items: [BOB] } })
})

describe.each(PAGES)('$name policy page', (page) => {
  it('renders the same form markup', async () => {
    const w = await mountPage(page, 'admins_only')
    expect(markup(w)).toMatchSnapshot()
  })

  it('shows the allowlist when the mode needs one', async () => {
    const w = await mountPage(page, 'admins_only')
    expect(w.find('section.allowlist').exists()).toBe(true)
    expect(w.text()).toContain('Ada')
    if (!page.allowlistAlways) {
      await w.find('input[type="radio"][value="everyone"]').setValue(true)
      expect(w.find('section.allowlist').exists()).toBe(false)
    }
  })

  it('picks a user from the search, removes one, toggles a group, and saves them', async () => {
    const w = await mountPage(page, 'admins_only')
    await w.find('section.allowlist input[type="search"]').setValue('bo')
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()
    expect(api.searchUsers).toHaveBeenCalledWith('bo')
    await w.find('.user-suggest').trigger('click')

    const rows = w.findAll('.picked-row')
    expect(rows.map((r) => r.find('.row-name').text())).toEqual(['Ada', 'Bob'])
    await rows[0].find('button').trigger('click')

    const checks = w.findAll('.group-check input[type="checkbox"]')
    await checks[0].setValue(false)
    await checks[1].setValue(true)

    await w.find('form').trigger('submit')
    await flushPromises()
    const body = page.save.mock.calls[0][0] as Record<string, unknown>
    expect(body[page.modeKey]).toBe('admins_only')
    expect(body[page.usersKey]).toEqual([6])
    expect(body[page.groupsKey]).toEqual([2])
  })

  it('does not suggest someone already on the list', async () => {
    api.searchUsers.mockResolvedValue({
      data: { items: [{ user_id: 5, display_name: 'Ada', email: 'ada@test.local', role: 'employee' }] },
    })
    const w = await mountPage(page, 'admins_only')
    await w.find('section.allowlist input[type="search"]').setValue('ad')
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()
    expect(w.find('.user-suggest').exists()).toBe(false)
  })
})

describe('the inbox pill', () => {
  it('marks the company inbox group on the API-token page only', async () => {
    const tokens = await mountPage(PAGES[0], 'admins_only')
    expect(tokens.find('.group-check .fh-pill').text()).toBe(en.admin_token_policy.groups_inbox)
    const approval = await mountPage(PAGES[2], 'admins_only')
    expect(approval.find('.group-check .fh-pill').exists()).toBe(false)
  })
})
