/* The load → edit → save skeleton the admin settings sections share. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { defineComponent, h, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const pushToast = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast, confirm: vi.fn() }) }))

import { useSettingsSection } from '@/composables/useSettingsSection'

function mountWith(load: () => Promise<{ data: { enabled: boolean } }>, save = load) {
  setActivePinia(createPinia())
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en: { saved: 'Saved it' } },
  })
  const enabled = ref(false)
  const afterSave = vi.fn()
  const Host = defineComponent({
    setup() {
      const section = useSettingsSection({
        load,
        save: () => save(),
        apply: (data) => {
          enabled.value = data.enabled
        },
        afterSave,
        savedToastKey: 'saved',
      })
      return () =>
        h('div', [
          h('span', { 'data-testid': 'state' }, section.loading.value ? 'loading' : 'ready'),
          h('span', { 'data-testid': 'error' }, section.errorMsg.value ?? ''),
          h('button', { 'data-testid': 'save', onClick: section.onSave }, 'save'),
        ])
    },
  })
  const w = mount(Host, { global: { plugins: [i18n] } })
  return { w, enabled, afterSave }
}

describe('useSettingsSection', () => {
  beforeEach(() => pushToast.mockReset())

  it('loads on mount, applies the response and clears loading', async () => {
    const { w, enabled } = mountWith(async () => ({ data: { enabled: true } }))
    expect(w.get('[data-testid="state"]').text()).toBe('loading')
    await flushPromises()
    expect(w.get('[data-testid="state"]').text()).toBe('ready')
    expect(enabled.value).toBe(true)
  })

  it('a save applies the stored values, runs afterSave and toasts once', async () => {
    const save = vi.fn(async () => ({ data: { enabled: false } }))
    const { w, enabled, afterSave } = mountWith(async () => ({ data: { enabled: true } }), save)
    await flushPromises()
    await w.get('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledTimes(1)
    expect(enabled.value).toBe(false)
    expect(afterSave).toHaveBeenCalledTimes(1)
    expect(pushToast).toHaveBeenCalledWith('Saved it', 'success')
    expect(w.get('[data-testid="error"]').text()).toBe('')
  })

  it('a failing load or save lands in errorMsg and never toasts', async () => {
    const boom = async () => {
      throw new Error('nope')
    }
    const { w } = mountWith(boom)
    await flushPromises()
    expect(w.get('[data-testid="state"]').text()).toBe('ready')
    expect(w.get('[data-testid="error"]').text()).not.toBe('')
    await w.get('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(pushToast).not.toHaveBeenCalled()
  })
})
