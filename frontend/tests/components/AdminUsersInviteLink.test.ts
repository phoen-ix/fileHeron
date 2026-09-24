/* "Copy link" on a pending invite rotated the invite's token - killing the
 * link already emailed - and only then wrote the clipboard. Nothing said the
 * old link died, and where the clipboard is unavailable (plain-HTTP installs)
 * or refuses after the await (Safari) nobody was left holding a working link.
 * It now asks first and SHOWS the new link. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'

import en from '@/i18n/locales/en.json'
import type { AdminInviteItem } from '@/types/api'

const INVITE: AdminInviteItem = {
  id: 7,
  email: 'newbie@example.com',
  target_role: 'client',
  state: 'pending',
  invited_by_id: 1,
  invited_by_display_name: 'Admin',
  initial_group_ids: null,
  created_at: '2026-09-20T10:00:00',
  expires_at: '2026-09-21T10:00:00',
}
const NEW_URL = 'https://files.example.com/register/fresh-token'

const regenerateInvite = vi.fn(async (_id: number) => ({ data: { url: NEW_URL } }))
vi.mock('@/api/admin', () => ({
  listUsers: vi.fn(async () => ({ data: { items: [], total: 0, page: 1, page_size: 50 } })),
  listInvites: vi.fn(async () => ({ data: { items: [INVITE], total: 1, page: 1, page_size: 50 } })),
  regenerateInvite: (id: number) => regenerateInvite(id),
  resendInvite: vi.fn(),
  revokeInvite: vi.fn(),
  activateInvite: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }), useRoute: () => ({ query: {} }) }))

let confirmAnswer = true
const confirm = vi.fn(async () => confirmAnswer)
const pushToast = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast, confirm }) }))

import AdminUsers from '@/views/AdminUsers.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminUsers, {
    global: {
      plugins: [i18n],
      stubs: {
        Pager: true,
        RouterLink: true,
        PasswordStrength: true,
        AdminPageHeader: { template: '<header><slot name="title" /><slot /><slot name="actions" /></header>' },
      },
    },
  })
}

function newLinkButton(w: ReturnType<typeof makeWrapper>) {
  const b = w.findAll('button').find((x) => x.text() === en.admin_users.invites.action.copy_link)
  if (!b) throw new Error('no New link button')
  return b
}

describe('AdminUsers - new invite link', () => {
  beforeEach(() => {
    regenerateInvite.mockClear()
    confirm.mockClear()
    confirmAnswer = true
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn(async () => Promise.reject(new Error('NotAllowedError'))) },
    })
  })

  it('asks first, and a No leaves the emailed link alone', async () => {
    confirmAnswer = false
    const w = makeWrapper()
    await flushPromises()
    await newLinkButton(w).trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalledOnce()
    expect(regenerateInvite).not.toHaveBeenCalled()
  })

  it('shows the new link even when the clipboard refuses', async () => {
    const w = makeWrapper()
    await flushPromises()
    await newLinkButton(w).trigger('click')
    await flushPromises()
    expect(regenerateInvite).toHaveBeenCalledWith(7)
    const field = w.find('.fresh-link input')
    expect((field.element as HTMLInputElement).value).toBe(NEW_URL)
    expect(pushToast).not.toHaveBeenCalledWith(expect.anything(), 'error')
  })
})
