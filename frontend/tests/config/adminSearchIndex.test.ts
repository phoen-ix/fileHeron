/* Unit tests for the admin Overview's static search registry. Guards the
 * things a hand-maintained list rots on: a labelKey that resolves in one
 * locale and not the other (vue-i18n renders the key string, silently), a
 * routeName the sidebar does not know (the result navigates nowhere), a
 * malformed hash, an uppercase keyword the lowercase matcher can never hit,
 * and a duplicate row. The Advanced-tunable coverage is pinned from the
 * backend side in `backend/tests/test_admin_search_index_pin.py`, which can
 * read the registry the entries mirror. */

import { describe, expect, it } from 'vitest'

import de from '@/i18n/locales/de.json'
import en from '@/i18n/locales/en.json'
import { ADMIN_ROUTE_NAMES } from '@/config/adminNav'
import { ADMIN_SEARCH_INDEX } from '@/config/adminSearchIndex'

/** Tunables the Advanced page shows that have NO `admin_advanced.keys.*`
 *  label in either locale today - the page falls back to the raw key. They
 *  are indexed anyway so the control stays findable by its keywords, and they
 *  are listed here EXPLICITLY so the gap is pinned, not hidden: the test below
 *  asserts these keys do NOT resolve, so the day someone adds the label this
 *  allowlist fails and gets shortened. */
const KNOWN_UNLABELLED: readonly string[] = [
  // Empty since the three tunables that rendered as raw key strings for four
  // releases (retention.public_link_attempt_days, retention.ip_block_days,
  // downloads.resume_credit_hours) got their labels. Add a key here only
  // together with the index row, and remove it the day the label lands.
]

function lookup(obj: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>(
    (acc, k) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[k] : undefined),
    obj,
  )
}

describe('ADMIN_SEARCH_INDEX', () => {
  it('is not vacuous', () => {
    expect(ADMIN_SEARCH_INDEX.length).toBeGreaterThanOrEqual(80)
  })

  it('resolves every labelKey to a string in BOTH en.json and de.json', () => {
    const unlabelled = new Set(KNOWN_UNLABELLED)
    for (const entry of ADMIN_SEARCH_INDEX) {
      if (unlabelled.has(entry.labelKey)) continue
      expect(typeof lookup(en, entry.labelKey), `en: ${entry.labelKey}`).toBe('string')
      expect(typeof lookup(de, entry.labelKey), `de: ${entry.labelKey}`).toBe('string')
    }
  })

  it('KNOWN_UNLABELLED names only keys that are indexed AND still missing from both locales', () => {
    const indexed = new Set(ADMIN_SEARCH_INDEX.map((e) => e.labelKey))
    for (const key of KNOWN_UNLABELLED) {
      // A stale allowlist entry (index row removed) is as misleading as a
      // missing one.
      expect(indexed.has(key), `${key} is allowlisted but not in the index`).toBe(true)
      // The label landed: shorten the allowlist rather than leave it lying.
      expect(lookup(en, key), `${key} now resolves in en - remove it from KNOWN_UNLABELLED`).toBeUndefined()
      expect(lookup(de, key), `${key} now resolves in de - remove it from KNOWN_UNLABELLED`).toBeUndefined()
    }
  })

  it('points every entry at a route the sidebar knows', () => {
    for (const entry of ADMIN_SEARCH_INDEX) {
      expect(ADMIN_ROUTE_NAMES.has(entry.routeName), `${entry.labelKey} → ${entry.routeName}`).toBe(true)
    }
  })

  it('has no duplicate (routeName, hash, labelKey) triple', () => {
    const seen = new Set<string>()
    for (const entry of ADMIN_SEARCH_INDEX) {
      const triple = `${entry.routeName}\u0000${entry.hash ?? ''}\u0000${entry.labelKey}`
      expect(seen.has(triple), `duplicate: ${triple.split('\u0000').join(' | ')}`).toBe(false)
      seen.add(triple)
    }
  })

  it('writes every hash with a leading #', () => {
    for (const entry of ADMIN_SEARCH_INDEX) {
      if (entry.hash === undefined) continue
      expect(entry.hash, `${entry.labelKey}`).toMatch(/^#\S+$/)
    }
  })

  it('keeps every keyword lowercase and non-empty', () => {
    for (const entry of ADMIN_SEARCH_INDEX) {
      for (const kw of entry.keywords ?? []) {
        expect(kw.length, `${entry.labelKey}: empty keyword`).toBeGreaterThan(0)
        expect(kw, `${entry.labelKey}: "${kw}"`).toBe(kw.toLowerCase())
        expect(kw, `${entry.labelKey}: "${kw}" has surrounding whitespace`).toBe(kw.trim())
      }
    }
  })
})
