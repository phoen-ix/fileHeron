import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

// retention.invite_days is OVERRIDDEN (effective 7, default 10) so we can test
// both "set a new value" and "reset to default → send null". Only the retention
// and storage groups render on this page now (config/adminTunablePlacement.ts).
const ITEMS = [
  {
    key: 'retention.invite_days',
    group: 'retention',
    kind: 'int',
    value: 7,
    default: 10,
    is_overridden: true,
    min: 1,
    max: 1000,
  },
  {
    key: 'storage.low_threshold_percent',
    group: 'storage',
    kind: 'int',
    value: 10,
    default: 10,
    is_overridden: false,
    min: null,
    max: null,
  },
]

const updateSpy = vi.fn(async (_payload: any) => ({ data: { items: ITEMS } }))

vi.mock('@/api/admin', () => ({
  getAdvancedSettings: vi.fn(async () => ({ data: { items: ITEMS } })),
  updateAdvancedSettings: (p: any) => updateSpy(p),
}))

import AdminSettingsAdvanced from '@/views/AdminSettingsAdvanced.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSettingsAdvanced, { global: { plugins: [i18n], stubs: { AdminPageHeader: { template: '<header><slot name="title" /><slot /><slot name="actions" /></header>' } } } })
}

describe('AdminSettingsAdvanced', () => {
  beforeEach(() => updateSpy.mockClear())

  it('renders grouped fields with labels + the default as placeholder', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(w.text()).toContain('Data retention')
    expect(w.text()).toContain('Pending invites (days)')
    const num = w.find('input[type="number"]')
    expect((num.element as HTMLInputElement).placeholder).toBe('10')
  })

  it('Save sends only the changed key', async () => {
    const w = makeWrapper()
    await flushPromises()
    await w.find('input[type="number"]').setValue('5')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(updateSpy).toHaveBeenCalledWith({ updates: { 'retention.invite_days': 5 } })
  })

  it('resetting an overridden value sends null', async () => {
    const w = makeWrapper()
    await flushPromises()
    await w.find('.reset-btn').trigger('click') // back to default 10
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(updateSpy).toHaveBeenCalledWith({ updates: { 'retention.invite_days': null } })
  })
})
