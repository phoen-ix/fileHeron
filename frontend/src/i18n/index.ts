import { createI18n } from 'vue-i18n'

import de from './locales/de.json'
import en from './locales/en.json'

export const SUPPORTED_LOCALES = ['en', 'de'] as const
export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number]

const LOCALE_STORAGE_KEY = 'fh.locale'

function isSupported(value: string | null | undefined): value is SupportedLocale {
  return !!value && (SUPPORTED_LOCALES as readonly string[]).includes(value)
}

function detectInitialLocale(): SupportedLocale {
  // Priority: localStorage (last explicit choice) → browser language → EN.
  // The user's saved server preference (User.locale) overrides this on
  // login via the auth store; see main.ts.
  if (typeof window !== 'undefined') {
    try {
      const stored = window.localStorage?.getItem(LOCALE_STORAGE_KEY)
      if (isSupported(stored)) return stored
    } catch {
      /* private mode / disabled storage — fall through to browser sniff */
    }
  }
  if (typeof navigator !== 'undefined') {
    const lang = navigator.language?.toLowerCase().slice(0, 2)
    if (isSupported(lang)) return lang
  }
  return 'en'
}

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: detectInitialLocale(),
  fallbackLocale: 'en',
  messages: { en, de },
})

export function setLocale(locale: SupportedLocale) {
  i18n.global.locale.value = locale
  document.documentElement.lang = locale
  // Persist so the same browser shows the same language on next load —
  // both for anonymous visitors (Login etc.) and as a fallback for
  // returning authenticated users before User.locale loads.
  if (typeof window !== 'undefined') {
    try {
      window.localStorage?.setItem(LOCALE_STORAGE_KEY, locale)
    } catch {
      /* storage unavailable; in-memory change still applied */
    }
  }
}
