import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it } from 'vitest'

import en from '@/i18n/locales/en.json'
import SiteFooter from '@/components/SiteFooter.vue'
import { useSiteStore } from '@/stores/site'

const RouterLinkStub = { props: ['to'], template: '<a class="rl"><slot/></a>' }

function makeWrapper() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(SiteFooter, {
    global: { plugins: [i18n], stubs: { RouterLink: RouterLinkStub } },
  })
}

describe('SiteFooter', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('renders nothing when no legal page is enabled', () => {
    const w = makeWrapper()
    expect(w.find('footer').exists()).toBe(false)
  })

  it('shows only the enabled legal links', async () => {
    const site = useSiteStore()
    site.legal = { imprint_enabled: true, privacy_enabled: false }
    const w = makeWrapper()
    await w.vm.$nextTick()
    expect(w.text()).toContain('Imprint')
    expect(w.text()).not.toContain('Privacy')
  })

  it('shows both links when both enabled', async () => {
    const site = useSiteStore()
    site.legal = { imprint_enabled: true, privacy_enabled: true }
    const w = makeWrapper()
    await w.vm.$nextTick()
    expect(w.findAll('a.rl').length).toBe(2)
  })
})
