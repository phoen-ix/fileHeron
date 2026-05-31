import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

// rate_limit.login is OVERRIDDEN (effective 7, default 10) so we can test
// both "set a new value" and "reset to default → send null".
const ITEMS = [
  {
    key: 'rate_limit.login',
    group: 'rate_limits',
    kind: 'int',
    value: 7,
    default: 10,
    is_overridden: true,
    min: 1,
    max: 1000,
  },
  {
    key: 'security.hibp_enabled',
    group: 'security',
    kind: 'bool',
    value: true,
    default: true,
    is_overridden: false,
    min: null,
    max: null,
  },
]

const updateSpy = vi.fn(async () => ({ data: { items: ITEMS } }))

vi.mock('@/api/admin', () => ({
  getAdvancedSettings: vi.fn(async () => ({ data: { items: ITEMS } })),
  updateAdvancedSettings: (p: any) => updateSpy(p),
}))

import AdminSettingsAdvanced from '@/views/AdminSettingsAdvanced.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSettingsAdvanced, { global: { plugins: [i18n] } })
}

describe('AdminSettingsAdvanced', () => {
  beforeEach(() => updateSpy.mockClear())

  it('renders grouped fields with labels + the default as placeholder', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(w.text()).toContain('Rate limits & lockout')
    expect(w.text()).toContain('Login attempts allowed per window')
    const num = w.find('input[type="number"]')
    expect((num.element as HTMLInputElement).placeholder).toBe('10')
  })

  it('Save sends only the changed key', async () => {
    const w = makeWrapper()
    await flushPromises()
    await w.find('input[type="number"]').setValue('5')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(updateSpy).toHaveBeenCalledWith({ updates: { 'rate_limit.login': 5 } })
  })

  it('resetting an overridden value sends null', async () => {
    const w = makeWrapper()
    await flushPromises()
    await w.find('.reset-btn').trigger('click') // back to default 10
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(updateSpy).toHaveBeenCalledWith({ updates: { 'rate_limit.login': null } })
  })
})
