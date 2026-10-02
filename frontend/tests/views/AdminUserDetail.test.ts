/* The admin user page shows a live login lockout and can lift it.
 *
 * Nothing could clear `users.locked_until` short of the user's own successful
 * login - which a lockout prevents - so an admin could only tell them to wait.
 * The page shows "locked until <time>" and an Unlock now button while the lock
 * is in force; the server answers the unlock with the user, so both go away. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ getUser: vi.fn(), unlockUser: vi.fn() }))
const empty = { data: { items: [], total: 0, page: 1, page_size: 50 } }
vi.mock('@/api/admin', () => ({
  getUser: (id: number) => api.getUser(id),
  unlockUser: (id: number) => api.unlockUser(id),
  adminListSessions: vi.fn(async () => empty),
  adminListFiles: vi.fn(async () => empty),
  listMailLog: vi.fn(async () => empty),
  adminDeleteFile: vi.fn(),
  adminRevokeSession: vi.fn(),
  adminRevokeUserSessions: vi.fn(),
  changeUserEmail: vi.fn(),
  erasePreflight: vi.fn(),
  eraseUser: vi.fn(),
  erasureReceiptPdf: vi.fn(),
  forcePasswordReset: vi.fn(),
  updateUser: vi.fn(),
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '7' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

import { useUiStore } from '@/stores/ui'
import AdminUserDetail from '@/views/AdminUserDetail.vue'

function user(lockedUntil: string | null) {
  return {
    id: 7, display_name: 'Lou', email: 'lou@test.local', role: 'employee', is_disabled: false,
    requires_2fa: false, quota_bytes: null, storage_used_bytes: 0,
    created_at: '2026-01-01T00:00:00', last_login_at: null, has_2fa: false,
    email_verified: true, locked_until: lockedUntil,
  }
}

function mountPage() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminUserDetail, {
    global: {
      plugins: [i18n],
      stubs: {
        RouterLink: { template: '<a><slot /></a>' },
        AdminPageHeader: { template: '<header><slot name="title" /><slot /><slot name="actions" /></header>' },
        StepUpDialog: true,
      },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.getUser.mockReset()
  api.unlockUser.mockReset()
})

describe('AdminUserDetail lockout', () => {
  it('shows the lock and an Unlock now button while it is in force', async () => {
    api.getUser.mockResolvedValue({ data: user('2030-01-01T10:00:00') })
    const w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="locked-pill"]').exists()).toBe(true)
    expect(w.find('[data-testid="unlock-btn"]').text()).toBe(en.admin_user_detail.unlock)
  })

  it('unlocks after a confirm, and the pill and button go', async () => {
    api.getUser.mockResolvedValue({ data: user('2030-01-01T10:00:00') })
    api.unlockUser.mockResolvedValue({ data: user(null) })
    const w = mountPage()
    await flushPromises()
    const ui = useUiStore()
    vi.spyOn(ui, 'confirm').mockResolvedValue(true)
    const toast = vi.spyOn(ui, 'pushToast')

    await w.find('[data-testid="unlock-btn"]').trigger('click')
    await flushPromises()
    expect(api.unlockUser).toHaveBeenCalledWith(7)
    expect(w.find('[data-testid="locked-pill"]').exists()).toBe(false)
    expect(w.find('[data-testid="unlock-btn"]').exists()).toBe(false)
    expect(toast).toHaveBeenCalledWith(en.admin_user_detail.unlocked_toast, 'success')
  })

  it('a declined confirm unlocks nothing', async () => {
    api.getUser.mockResolvedValue({ data: user('2030-01-01T10:00:00') })
    const w = mountPage()
    await flushPromises()
    vi.spyOn(useUiStore(), 'confirm').mockResolvedValue(false)
    await w.find('[data-testid="unlock-btn"]').trigger('click')
    await flushPromises()
    expect(api.unlockUser).not.toHaveBeenCalled()
  })

  it('an account that is not locked shows neither', async () => {
    api.getUser.mockResolvedValue({ data: user(null) })
    const w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="locked-pill"]').exists()).toBe(false)
    expect(w.find('[data-testid="unlock-btn"]').exists()).toBe(false)
  })
})
