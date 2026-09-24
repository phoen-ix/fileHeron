/* Below 720px the header's nav is hidden (`.app-nav { display: none }`), and the
 * user menu used to hold only Admin, Account and Logout - so on a phone a
 * recipient had no control that led to their inbox. The menu now carries the
 * same destinations, shown on narrow screens only (the media query itself is
 * CSS; this pins that the links exist in the menu at all). */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

import AppHeader from '@/components/AppHeader.vue'
import { useAuthStore } from '@/stores/auth'

const RouterLinkStub = {
  props: ['to'],
  template: '<a :data-to="typeof to === \'string\' ? to : to.name"><slot /></a>',
}

function mountHeader(canApprove: boolean) {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.user = {
    id: 1,
    display_name: 'Ada Lovelace',
    role: 'client',
    can_approve_shares: canApprove,
  } as unknown as typeof auth.user
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AppHeader, {
    global: {
      plugins: [i18n],
      stubs: { RouterLink: RouterLinkStub, NotificationBell: true, BrandMark: true, BrandLogo: true },
    },
  })
}

describe('AppHeader user menu', () => {
  it('offers the header destinations for narrow screens', async () => {
    const w = mountHeader(true)
    await w.find('.user-trigger').trigger('click')
    const menu = w.find('.user-pop')
    const targets = menu.findAll('a.narrow-only').map((a) => a.attributes('data-to'))
    expect(targets).toEqual(['outbox', 'inbox', 'approvals', 'share-create'])
  })

  it('leaves Approvals out for someone who cannot approve', async () => {
    const w = mountHeader(false)
    await w.find('.user-trigger').trigger('click')
    const targets = w.find('.user-pop').findAll('a.narrow-only').map((a) => a.attributes('data-to'))
    expect(targets).toEqual(['outbox', 'inbox', 'share-create'])
  })
})
