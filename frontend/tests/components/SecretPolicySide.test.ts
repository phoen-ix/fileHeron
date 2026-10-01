/* The Secrets policy tab has two gates of the same shape. The second - sending
 * outside the organisation - is one a client never passes, through the mode or
 * the allowlist, so it must not offer what the backend will refuse: the shared
 * "Clients only when allowed below" hint, or a client in the allowlist search. */
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ searchUsers: vi.fn() }))
vi.mock('@/api/users', () => ({ searchUsers: (q: string) => api.searchUsers(q) }))

import SecretPolicySide from '@/components/admin/SecretPolicySide.vue'
import type { SecretAllowedUser } from '@/types/api'

const CLIENT = { user_id: 7, display_name: 'Anna Client', email: 'anna@c.test', role: 'client' }
const EMPLOYEE = { user_id: 8, display_name: 'Ben Staff', email: 'ben@s.test', role: 'employee' }

function mountSide(outside: boolean, users: SecretAllowedUser[] = []) {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(SecretPolicySide, {
    props: {
      modelValue: { mode: 'admins_only', users, groups: [] },
      modes: outside
        ? ['employees_admins', 'admins_only']
        : ['everyone', 'employees_admins', 'admins_only'],
      groups: [],
      name: outside ? 'external-mode' : 'send-mode',
      legend: 'legend',
      help: 'help',
      outside,
    },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  api.searchUsers.mockReset()
  api.searchUsers.mockResolvedValue({ data: { items: [CLIENT, EMPLOYEE] } })
})
afterEach(() => {
  vi.useRealTimers()
})

describe('SecretPolicySide', () => {
  it('never tells the outside gate that a client can be allowed', () => {
    const text = mountSide(true).text()
    expect(text).toContain('Every employee and admin. Never a client.')
    expect(text).toContain('Employees only when allowed below. Never a client.')
    expect(text).not.toContain('Clients only when allowed below.')
  })

  it('keeps the shared hints on the send gate', () => {
    const text = mountSide(false).text()
    expect(text).toContain('Clients only when allowed below.')
    expect(text).not.toContain('Never a client.')
  })

  it('leaves clients out of the outside allowlist search', async () => {
    const w = mountSide(true)
    await w.find('input[type=search]').setValue('an')
    vi.advanceTimersByTime(250)
    await flushPromises()
    const names = w.findAll('.suggest .row-name').map((n) => n.text())
    expect(names).toEqual(['Ben Staff'])
  })

  it('offers clients on the send gate', async () => {
    const w = mountSide(false)
    await w.find('input[type=search]').setValue('an')
    vi.advanceTimersByTime(250)
    await flushPromises()
    const names = w.findAll('.suggest .row-name').map((n) => n.text())
    expect(names).toEqual(['Anna Client', 'Ben Staff'])
  })

  it('says a client already on the outside list has no effect', () => {
    const listed: SecretAllowedUser[] = [
      { id: 7, display_name: 'Anna Client', email: 'anna@c.test', role: 'client' },
    ]
    expect(mountSide(true, listed).find('.picked-row').text()).toContain(
      'a client: never allowed here',
    )
    expect(mountSide(false, listed).find('.picked-row').text()).not.toContain('never allowed')
  })
})
