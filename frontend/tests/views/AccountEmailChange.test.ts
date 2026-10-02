/* The account page shows a pending email change and can cancel it.
 *
 * Nothing exposed a pending change: once requested it was invisible until its
 * link expired 24h later, and the self-cancel route had no caller. The page
 * reads GET /account/email, shows the address and deadline, and cancels through
 * DELETE /account/email after a confirm. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  getPendingEmailChange: vi.fn(),
  cancelOwnEmailChange: vi.fn(),
  requestEmailChange: vi.fn(),
}))
vi.mock('@/api/account', () => ({
  listSessions: vi.fn(async () => ({ data: { items: [] } })),
  getPendingEmailChange: () => api.getPendingEmailChange(),
  cancelOwnEmailChange: () => api.cancelOwnEmailChange(),
  requestEmailChange: (p: unknown) => api.requestEmailChange(p),
}))
vi.mock('@/api/twoFactor', () => ({
  getStatus: vi.fn(async () => ({ data: { enabled: false, recovery_codes_remaining: 0 } })),
}))
vi.mock('@/composables/useScrollSpy', () => ({
  useScrollSpy: () => ({ active: ref(''), lockTo: vi.fn() }),
}))

import Account from '@/views/Account.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

const PENDING = { new_email: 'new@test.local', expires_at: '2030-01-01T10:00:00' }

function mountPage(canChange = true) {
  const auth = useAuthStore()
  auth.user = {
    id: 1, email: 'me@test.local', display_name: 'Ada', role: 'employee', locale: 'en',
    default_landing_page: null, admin_nav_collapse_mode: null, can_change_own_email: canChange,
  } as unknown as typeof auth.user
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(Account, {
    global: {
      plugins: [i18n],
      stubs: {
        RouterLink: true, SectionQuickNav: true, ApiTokenPanel: true, NotificationPreferences: true,
        OIDCConnectPanel: true, PasswordStrength: true, SessionRow: true, WebAuthnPanel: true,
      },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.getPendingEmailChange.mockReset()
  api.cancelOwnEmailChange.mockReset()
  api.requestEmailChange.mockReset()
})

describe('Account email change', () => {
  it('shows a pending change with its address and a cancel button', async () => {
    api.getPendingEmailChange.mockResolvedValue({ data: { pending: PENDING } })
    const w = mountPage()
    await flushPromises()
    const notice = w.find('[data-testid="email-pending"]')
    expect(notice.text()).toContain('new@test.local')
    expect(notice.find('[data-testid="email-pending-cancel"]').text()).toBe(
      en.account.email_change_cancel,
    )
  })

  it('cancels after a confirm and drops the notice', async () => {
    api.getPendingEmailChange.mockResolvedValue({ data: { pending: PENDING } })
    api.cancelOwnEmailChange.mockResolvedValue({ data: { ok: true, cancelled: 1 } })
    const w = mountPage()
    await flushPromises()
    const ui = useUiStore()
    const confirm = vi.spyOn(ui, 'confirm').mockResolvedValue(true)
    const toast = vi.spyOn(ui, 'pushToast')

    await w.find('[data-testid="email-pending-cancel"]').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalledOnce()
    expect(api.cancelOwnEmailChange).toHaveBeenCalledOnce()
    expect(w.find('[data-testid="email-pending"]').exists()).toBe(false)
    expect(toast).toHaveBeenCalledWith(en.account.email_change_cancelled, 'success')
  })

  it('keeps the change when the confirm is declined', async () => {
    api.getPendingEmailChange.mockResolvedValue({ data: { pending: PENDING } })
    const w = mountPage()
    await flushPromises()
    vi.spyOn(useUiStore(), 'confirm').mockResolvedValue(false)
    await w.find('[data-testid="email-pending-cancel"]').trigger('click')
    await flushPromises()
    expect(api.cancelOwnEmailChange).not.toHaveBeenCalled()
    expect(w.find('[data-testid="email-pending"]').exists()).toBe(true)
  })

  it('a request that waits for confirmation shows the new pending change', async () => {
    api.getPendingEmailChange
      .mockResolvedValueOnce({ data: { pending: null } })
      .mockResolvedValueOnce({ data: { pending: PENDING } })
    api.requestEmailChange.mockResolvedValue({ data: { ok: true, applied: false, mode: 'verify_new' } })
    const w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="email-pending"]').exists()).toBe(false)

    await w.find('#acc-email-new').setValue('new@test.local')
    await w.find('#acc-email-pw').setValue('pw')
    await w.find('#email form').trigger('submit')
    await flushPromises()
    expect(api.getPendingEmailChange).toHaveBeenCalledTimes(2)
    expect(w.find('[data-testid="email-pending"]').text()).toContain('new@test.local')
  })

  it('shows a pending change even where self-service is off, without the form', async () => {
    api.getPendingEmailChange.mockResolvedValue({ data: { pending: PENDING } })
    const w = mountPage(false)
    await flushPromises()
    expect(w.find('[data-testid="email-pending"]').exists()).toBe(true)
    expect(w.find('#acc-email-new').exists()).toBe(false)
  })

  it('has no email section without self-service or a pending change', async () => {
    api.getPendingEmailChange.mockResolvedValue({ data: { pending: null } })
    const w = mountPage(false)
    await flushPromises()
    expect(w.find('#email').exists()).toBe(false)
  })
})
