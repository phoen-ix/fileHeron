/* "Activate account directly" told the admin "They can sign in via SSO
 * immediately" and toasted "They can sign in immediately." The account it
 * makes has no usable password, and staff are not linked to SSO on their own,
 * so the person could not get in at all (found by use case U3, 2026-11-21).
 * Activating now hands the admin a one-time set-password link, shown on the
 * page - not inside the invites section, which is gone once the last pending
 * invitation was activated. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  listUsers: vi.fn(),
  listInvites: vi.fn(),
  activateInvite: vi.fn(),
}))
vi.mock('@/api/admin', () => ({
  listUsers: (p: unknown) => api.listUsers(p),
  listInvites: () => api.listInvites(),
  activateInvite: (id: number, p: unknown) => api.activateInvite(id, p),
  regenerateInvite: vi.fn(),
  resendInvite: vi.fn(),
  revokeInvite: vi.fn(),
  createUserDirect: vi.fn(),
}))
vi.mock('@/api/account', () => ({ inviteUser: vi.fn() }))
vi.mock('@/api/groups', () => ({ listGroups: vi.fn(async () => ({ data: { items: [] } })) }))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ name: 'admin-users' }),
}))

import AdminUsers from '@/views/AdminUsers.vue'
import { useUiStore } from '@/stores/ui'

const LINK = 'https://files.example/reset-password/tok-123'
const invite = {
  id: 7,
  email: 'ada@example.com',
  target_role: 'employee',
  status: 'pending',
  created_at: '2030-01-01T09:00:00',
  expires_at: '2030-01-08T09:00:00',
  invited_by: null,
  initial_group_ids: [],
}

async function mountPage() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(AdminUsers, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: { AdminPageHeader: { template: '<header><slot name="actions" /></header>' } },
    },
  })
  await flushPromises()
  return w
}

beforeEach(() => {
  document.body.innerHTML = ''
  api.listUsers.mockReset().mockResolvedValue({ data: { items: [], total: 0 } })
  api.listInvites
    .mockReset()
    .mockResolvedValueOnce({ data: { items: [invite] } })
    .mockResolvedValue({ data: { items: [] } })
  api.activateInvite.mockReset().mockResolvedValue({
    data: {
      id: 3,
      email: invite.email,
      set_password_url: LINK,
      set_password_expires_at: '2030-01-01T10:00:00',
    },
  })
})

describe('AdminUsers: activate an invitation directly', () => {
  it('says the account has no password, and never that they can sign in at once', async () => {
    const w = await mountPage()
    await w
      .findAll('button')
      .filter((b) => b.text() === 'Activate')[0]
      .trigger('click')

    const dialog = document.body.querySelector('[role="dialog"]')!.textContent ?? ''
    expect(dialog).toContain('no password yet')
    expect(dialog).not.toMatch(/sign in via SSO immediately/i)
    expect(en.admin_users.invites.toast.activated).not.toMatch(/immediately/i)
  })

  it('shows the set-password link after activating, even with no invitation left', async () => {
    const w = await mountPage()
    await w
      .findAll('button')
      .filter((b) => b.text() === 'Activate')[0]
      .trigger('click')
    const confirm = [...document.body.querySelectorAll('[role="dialog"] button')].find(
      (b) => b.textContent?.trim() === 'Activate now',
    ) as HTMLButtonElement
    confirm.click()
    await flushPromises()

    expect(api.activateInvite).toHaveBeenCalledWith(7, { display_name: 'Ada' })
    // The last invitation is gone, and its section with it.
    expect(w.find('.invites-section').exists()).toBe(false)
    const panel = w.get('[data-testid="activated-link"]')
    expect(panel.text()).toContain('ada@example.com')
    expect(panel.text()).toContain('no password yet')
    expect((panel.get('input').element as HTMLInputElement).value).toBe(LINK)
    expect(useUiStore().toasts.map((t) => t.text)).toContain('Account activated.')

    await panel
      .findAll('button')
      .filter((b) => b.text() === 'Done')[0]
      .trigger('click')
    expect(w.find('[data-testid="activated-link"]').exists()).toBe(false)
  })
})
