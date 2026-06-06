import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const getLegal = vi.fn(async (_kind: string) => ({
  data: { enabled: true, html_en: '<h1>Imprint</h1><p>Acme Ltd</p>', html_de: '<h1>Impressum</h1>' },
}))
vi.mock('@/api/legal', () => ({ getLegal: (k: string) => getLegal(k) }))

let routeName = 'imprint'
vi.mock('vue-router', () => ({ useRoute: () => ({ name: routeName }) }))

import LegalPage from '@/views/LegalPage.vue'

function makeWrapper() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(LegalPage, { global: { plugins: [i18n] } })
}

describe('LegalPage', () => {
  beforeEach(() => {
    routeName = 'imprint'
    getLegal.mockClear()
  })

  it('fetches by kind and renders the sanitised HTML', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(getLegal).toHaveBeenCalledWith('imprint')
    expect(w.html()).toContain('<h1>Imprint</h1>')
    expect(w.text()).toContain('Acme Ltd')
  })

  it('shows the not-available state when disabled', async () => {
    getLegal.mockResolvedValueOnce({ data: { enabled: false, html_en: '', html_de: '' } })
    const w = makeWrapper()
    await flushPromises()
    expect(w.text()).toContain('not been published')
  })

  it('resolves the privacy kind from the route name', async () => {
    routeName = 'privacy'
    makeWrapper()
    await flushPromises()
    expect(getLegal).toHaveBeenCalledWith('privacy')
  })
})
