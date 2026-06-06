/* Loads the locale JSON files through vue-i18n's message compiler and asserts
 * that messages containing literal "@" render correctly. Without escaping,
 * vue-i18n treats "@" as a linked-message prefix and throws "Invalid linked
 * format" on first render - a real regression we already shipped once.
 *
 * This test exercises the same compilation path as the running app. */

import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import de from '@/i18n/locales/de.json'
import en from '@/i18n/locales/en.json'

function makeI18n(locale: 'en' | 'de') {
  return createI18n({
    legacy: false,
    locale,
    fallbackLocale: 'en',
    messages: { en, de },
  })
}

describe('locale messages compile + render', () => {
  it('en login.email_placeholder renders the literal "@"', () => {
    const i = makeI18n('en')
    const text = i.global.t('login.email_placeholder')
    expect(text).toContain('@')
    expect(text).not.toContain("{'")
  })

  it('de login.email_placeholder renders the literal "@"', () => {
    const i = makeI18n('de')
    const text = i.global.t('login.email_placeholder')
    expect(text).toContain('@')
    expect(text).not.toContain("{'")
  })

  it('every message in en.json compiles without throwing', () => {
    const i = makeI18n('en')
    const errors: string[] = []
    function walk(prefix: string, obj: unknown) {
      if (typeof obj === 'string') {
        try {
          // Force compile by calling t().
          i.global.t(prefix)
        } catch (err) {
          errors.push(`${prefix}: ${(err as Error).message}`)
        }
      } else if (obj && typeof obj === 'object') {
        for (const [k, v] of Object.entries(obj)) {
          walk(prefix ? `${prefix}.${k}` : k, v)
        }
      }
    }
    walk('', en)
    expect(errors).toEqual([])
  })

  it('every message in de.json compiles without throwing', () => {
    const i = makeI18n('de')
    const errors: string[] = []
    function walk(prefix: string, obj: unknown) {
      if (typeof obj === 'string') {
        try {
          i.global.t(prefix)
        } catch (err) {
          errors.push(`${prefix}: ${(err as Error).message}`)
        }
      } else if (obj && typeof obj === 'object') {
        for (const [k, v] of Object.entries(obj)) {
          walk(prefix ? `${prefix}.${k}` : k, v)
        }
      }
    }
    walk('', de)
    expect(errors).toEqual([])
  })

  it('en + de keysets are identical (no untranslated holes)', () => {
    function flatKeys(obj: unknown, prefix = ''): string[] {
      if (typeof obj === 'string') return [prefix]
      if (obj && typeof obj === 'object') {
        return Object.entries(obj).flatMap(([k, v]) =>
          flatKeys(v, prefix ? `${prefix}.${k}` : k),
        )
      }
      return []
    }
    const enKeys = new Set(flatKeys(en))
    const deKeys = new Set(flatKeys(de))
    const onlyEn = [...enKeys].filter((k) => !deKeys.has(k))
    const onlyDe = [...deKeys].filter((k) => !enKeys.has(k))
    expect(onlyEn).toEqual([])
    expect(onlyDe).toEqual([])
  })
})
