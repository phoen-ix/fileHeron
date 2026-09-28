/* The share page repeats the words of the preset the sender clicked. */
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import de from '@/i18n/locales/de.json'
import en from '@/i18n/locales/en.json'
import { afterReadyLabel, durationLabel } from '@/utils/duration'

function tFor(locale: 'en' | 'de') {
  const i = createI18n({ legacy: false, locale, fallbackLocale: 'en', messages: { en, de } })
  return i.global.t as unknown as Parameters<typeof durationLabel>[1]
}

describe('durationLabel', () => {
  it('uses the preset words for the preset durations', () => {
    const t = tFor('en')
    expect(durationLabel(3600, t)).toBe(en.expiry.presets['1h'])
    expect(durationLabel(7 * 86400, t)).toBe(en.expiry.presets['7d'])
    expect(durationLabel(365 * 86400, t)).toBe(en.expiry.presets['1y'])
  })

  it('falls back to days or hours, with the right plural', () => {
    const t = tFor('en')
    expect(durationLabel(2 * 86400, t)).toBe('2 days')
    expect(durationLabel(7200, t)).toBe('2 hours')
    expect(durationLabel(3 * 3600 + 60, t)).toBe('3 hours')
  })

  it('says it counts from ready, in both languages', () => {
    expect(afterReadyLabel(3600, tFor('en'))).toBe('1 hour after the files are ready')
    expect(afterReadyLabel(86400, tFor('de'))).toContain(de.expiry.presets['1d'])
  })
})
