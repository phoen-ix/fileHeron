/* A native datetime-local reports '' while a segment is cleared or half-typed.
 * The picker mapped '' to null - which means "never expires" - so clearing the
 * field on an API-token form minted a permanent credential while the 90-day
 * preset stayed highlighted. */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import en from '@/i18n/locales/en.json'
import ExpiryPicker from '@/components/ExpiryPicker.vue'

function mountPicker(modelValue: string | null) {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(ExpiryPicker, { props: { modelValue }, global: { plugins: [i18n] } })
}

describe('ExpiryPicker', () => {
  it('does not turn a cleared field into "never"', async () => {
    const w = mountPicker('2027-12-01T10:00:00')
    const input = w.find('input[type="datetime-local"]')
    await input.setValue('')
    const emitted = (w.emitted('update:modelValue') ?? []).map((e) => e[0])
    expect(emitted).not.toContain(null)
    await input.trigger('blur')
    expect((input.element as HTMLInputElement).value).toBe('2027-12-01T10:00')
  })

  it('still accepts a typed date', async () => {
    const w = mountPicker('2027-12-01T10:00:00')
    await w.find('input[type="datetime-local"]').setValue('2028-01-02T09:30')
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual(['2028-01-02T09:30:00'])
  })

  it('"never" comes only from the Never preset', async () => {
    const w = mountPicker('2027-12-01T10:00:00')
    const never = w.findAll('button').find((b) => b.text() === en.expiry.presets.never)!
    await never.trigger('click')
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([null])
  })

  it('from ready: a preset emits its duration and says when the clock starts', async () => {
    // A "1 hour" share holding a 20 GB upload expired mid-transfer: the preset
    // counted from the click. On the new-share form it counts from ready.
    setActivePinia(createPinia())
    const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
    const w = mount(ExpiryPicker, {
      props: { modelValue: undefined, fromReady: true },
      global: { plugins: [i18n] },
    })
    expect(w.emitted('update:relative')?.at(-1)).toEqual([7 * 24 * 3600])

    const hour = w.findAll('button').find((b) => b.text() === en.expiry.presets['1h'])!
    await hour.trigger('click')
    expect(w.emitted('update:relative')?.at(-1)).toEqual([3600])
    expect(w.find('.hint').text()).toBe('1 hour after the files are ready')

    await w.find('input[type="datetime-local"]').setValue('2031-01-02T09:30')
    expect(w.emitted('update:relative')?.at(-1)).toEqual([null])
  })

  it('without from-ready, a preset stays an exact time', async () => {
    const w = mountPicker('2027-12-01T10:00:00')
    const hour = w.findAll('button').find((b) => b.text() === en.expiry.presets['1h'])!
    await hour.trigger('click')
    expect((w.emitted('update:relative') ?? []).every((e) => e[0] === null)).toBe(true)
    expect(w.find('.hint').text()).toMatch(/^in /)
  })
})
