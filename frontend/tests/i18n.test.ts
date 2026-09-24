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

describe('counted messages use plural forms', () => {
  it('picks the singular and the plural in both locales', () => {
    const en_ = makeI18n('en').global
    const de_ = makeI18n('de').global
    expect(en_.t('share_list.bulk.toast.all_expired', { n: 1 }, 1)).toBe('1 share expired.')
    expect(en_.t('share_list.bulk.toast.all_expired', { n: 3 }, 3)).toBe('3 shares expired.')
    expect(de_.t('approvals.file_count', { n: 1 }, 1)).toBe('1 Datei')
    expect(de_.t('approvals.file_count', { n: 2 }, 2)).toBe('2 Dateien')
    expect(
      en_.t('notif_bell.headline.share_created', { sender_name: 'Ada', file_count: 1 }, 1),
    ).toBe('Ada sent you 1 file.')
  })

  // A ratchet, not a style note: a new "{n} thing(s)" string fails here. What
  // remains is listed with the reason it is not a single plural form.
  it('no new "(s)" strings', () => {
    const remaining = new Set([
      // Labels, not counts.
      'admin_share_defaults.intro',
      'share_create.notify_recipients_label',
      // Two counts in one sentence - needs rewording into two messages, not a
      // plural switch.
      'admin_maintenance.activity',
      'admin_system.update.postpone.active_notice',
      'admin_system.update.postpone.waiting',
      'admin_user_detail.erase_preflight_shares',
      'admin_scan_guard.live_counts',
      'admin_backup.confirm_body',
      'admin_imap.fetch_empty',
      'admin_inbox.fetch_empty',
      // Admin-only single counts, not yet converted.
      'admin_backup.sum_admins_installed',
      'admin_user_detail.erased_toast',
      'admin_user_detail.erase_preflight_files',
    ])
    const found: string[] = []
    const walk = (node: unknown, path: string[]) => {
      if (typeof node === 'string') {
        if (node.includes('(s)') || node.includes('(es)')) found.push(path.join('.'))
      } else if (node && typeof node === 'object') {
        for (const [k, v] of Object.entries(node)) walk(v, [...path, k])
      }
    }
    walk(en, [])
    expect(found.filter((k) => !remaining.has(k))).toEqual([])
  })
})
