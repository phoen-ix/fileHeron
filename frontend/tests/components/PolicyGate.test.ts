/* components/admin/PolicyGate.vue: the "who may" block the policy pages
 * share. The pages' own tests (PolicyPages.test.ts) pin the rendered markup;
 * this pins the component's contract - it emits the whole model on every
 * change, hides the allowlist only under "everyone", and shows the inbox pill
 * only when the page passes a label for it. */
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

vi.mock('@/api/users', () => ({
  searchUsers: async () => ({
    data: { items: [{ user_id: 9, display_name: 'Cy', email: 'cy@test.local', role: 'employee' }] },
  }),
}))

import PolicyGate from '@/components/admin/PolicyGate.vue'

const LABELS = {
  mode: 'Mode', heading: 'Allowlist', help: 'Always allowed', users: 'Users',
  usersPlaceholder: 'Search', groups: 'Groups',
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
    expect(w.emitted('update:modelValue')?.[0]).toEqual([{ mode: 'everyone', users: [], groups: [] }])

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
