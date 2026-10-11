/* The passkey sign-in switch (v2.27.0) lives on the 2FA policy page: it loads
 * from the policy, saves with it, and warns about the desktop app while on. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const policy = (passkey_signin: boolean) => ({
  required_roles: [],
  required_group_ids: [],
  required_groups: [],
  is_kv_overridden: true,
  passkey_signin,
})

const getTwofaPolicy = vi.fn()
const updateTwofaPolicy = vi.fn()
vi.mock('@/api/admin', () => ({
  getTwofaPolicy: () => getTwofaPolicy(),
  updateTwofaPolicy: (payload: unknown) => updateTwofaPolicy(payload),
}))
vi.mock('@/api/groups', () => ({
  listGroups: vi.fn(async () => ({ data: { items: [] } })),
}))
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ refreshMe: vi.fn(async () => {}), user: null }),
}))
vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({ pushToast: vi.fn() }),
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

import AdminSettingsTwofa from '@/views/AdminSettingsTwofa.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSettingsTwofa, {
    global: { plugins: [i18n], stubs: { AdminPageHeader: true } },
  })
}

describe('AdminSettingsTwofa passkey sign-in switch', () => {
  beforeEach(() => {
    getTwofaPolicy.mockReset()
    updateTwofaPolicy.mockReset()
  })

  it('shows the saved state and saves a change with the policy', async () => {
    getTwofaPolicy.mockResolvedValueOnce({ data: policy(false) })
    updateTwofaPolicy.mockResolvedValueOnce({ data: policy(true) })
    const w = makeWrapper()
    await flushPromises()

    const box = w.find<HTMLInputElement>('[data-testid="passkey-signin-switch"]')
    expect(box.element.checked).toBe(false)
    expect(w.text()).not.toContain(en.admin_twofa_policy.passkey_desktop_warning)

    await box.setValue(true)
    expect(w.text()).toContain(en.admin_twofa_policy.passkey_desktop_warning)
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(updateTwofaPolicy).toHaveBeenCalledWith({
      required_roles: [],
      required_group_ids: [],
      passkey_signin: true,
    })
  })

  it('loads a switch that is on', async () => {
    getTwofaPolicy.mockResolvedValueOnce({ data: policy(true) })
    const w = makeWrapper()
    await flushPromises()

    expect(w.find<HTMLInputElement>('[data-testid="passkey-signin-switch"]').element.checked).toBe(
      true,
    )
  })
})
