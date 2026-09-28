/* The "Recipients without an account" switches on the Public links page: they
 * render, the second one is only usable while the first is on, and a save
 * sends both - never an "ask to invite" left on under a disabled feature. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  getPublicLinkPolicy: vi.fn(),
  updatePublicLinkPolicy: vi.fn(),
}))
vi.mock('@/api/admin', () => ({
  getPublicLinkPolicy: () => api.getPublicLinkPolicy(),
  updatePublicLinkPolicy: (p: unknown) => api.updatePublicLinkPolicy(p),
}))
vi.mock('@/api/groups', () => ({ listGroups: async () => ({ data: { items: [] } }) }))
vi.mock('@/api/users', () => ({ searchUsers: async () => ({ data: { items: [] } }) }))

import { useAuthStore } from '@/stores/auth'
import AdminSettingsPublicLinks from '@/views/AdminSettingsPublicLinks.vue'

function policy(enabled: boolean, offer: boolean) {
  return {
    data: {
      mode: 'employees_admins',
      allowed_user_ids: [],
      allowed_group_ids: [],
      allowed_users: [],
      allowed_groups: [],
      external_recipients_enabled: enabled,
      external_recipients_offer_invite: offer,
    },
  }
}

async function mountPage() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(AdminSettingsPublicLinks, {
    global: {
      plugins: [i18n],
      stubs: {
        AdminPageHeader: { template: '<div><slot /><slot name="actions" /></div>' },
        TunableFields: true,
      },
    },
  })
  await flushPromises()
  return w
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.spyOn(useAuthStore(), 'refreshMe').mockResolvedValue(undefined as never)
  api.getPublicLinkPolicy.mockReset()
  api.updatePublicLinkPolicy.mockReset()
})

describe('AdminSettingsPublicLinks - recipients without an account', () => {
  it('renders both switches off by default, the second unusable', async () => {
    api.getPublicLinkPolicy.mockResolvedValue(policy(false, false))
    const w = await mountPage()

    expect(w.find('#external-recipients').exists()).toBe(true)
    expect(w.text()).toContain(en.admin_public_link_policy.external.title)
    const enabled = w.find('[data-testid="external-enabled"]')
    const offer = w.find('[data-testid="external-offer-invite"]')
    expect((enabled.element as HTMLInputElement).checked).toBe(false)
    expect(offer.attributes('disabled')).toBeDefined()
  })

  it('saves both, and never an invite offer under a disabled feature', async () => {
    api.getPublicLinkPolicy.mockResolvedValue(policy(true, true))
    api.updatePublicLinkPolicy.mockImplementation(async (p: Record<string, unknown>) =>
      policy(p.external_recipients_enabled as boolean, p.external_recipients_offer_invite as boolean),
    )
    const w = await mountPage()
    expect(w.find('[data-testid="external-offer-invite"]').attributes('disabled')).toBeUndefined()

    await w.find('[data-testid="external-enabled"]').setValue(false)
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(api.updatePublicLinkPolicy.mock.calls[0][0]).toMatchObject({
      external_recipients_enabled: false,
      external_recipients_offer_invite: false,
    })
  })
})
