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
  return path
    .split('.')
    .reduce<unknown>(
      (acc, k) =>
        acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[k] : undefined,
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
      expect(
        lookup(en, key),
        `${key} now resolves in en - remove it from KNOWN_UNLABELLED`,
      ).toBeUndefined()
      expect(
        lookup(de, key),
        `${key} now resolves in de - remove it from KNOWN_UNLABELLED`,
      ).toBeUndefined()
    }
  })

  it('points every entry at a route the sidebar knows', () => {
    for (const entry of ADMIN_SEARCH_INDEX) {
      expect(ADMIN_ROUTE_NAMES.has(entry.routeName), `${entry.labelKey} → ${entry.routeName}`).toBe(
        true,
      )
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

/* A hash is only useful if the route's view renders `id="<hash>"`. Four
 * entries kept pointing `#file-preview` and `#share-defaults` at General after
 * those sections moved to Transfers, so the search landed on a page without
 * the anchor; the shape check above (`#\S+`) could not see it. Resolve each
 * routeName to its view through the router source and read the view's ids -
 * one level of `@/components/...` imports included, for a view that renders a
 * section through a child. Sources come in through `import.meta.glob` (typed
 * by vite/client, resolves under vue-tsc), like adminNav.test.ts. */
const SOURCES = import.meta.glob(
  ['/src/router/index.ts', '/src/views/*.vue', '/src/components/**/*.vue'],
  { query: '?raw', import: 'default', eager: true },
) as Record<string, string>

function viewSourceFor(routeName: string): string {
  const router = SOURCES['/src/router/index.ts']
  // The route DEFINITION (name, then component), not a `redirect: { name }`
  // that happens to mention the route first.
  const m = router.match(
    new RegExp(
      `name: '${routeName}',\\s*\\n\\s*component: \\(\\) => import\\('@/views/([\\w.-]+\\.vue)'\\)`,
    ),
  )
  expect(
    m,
    `route ${routeName} has no name-then-component definition in router/index.ts`,
  ).not.toBeNull()
  const source = SOURCES[`/src/views/${m![1]}`]
  expect(source, `no source for /src/views/${m![1]}`).toBeDefined()
  return source
}

function idsRenderedBy(source: string): Set<string> {
  const ids = new Set<string>()
  if (source.includes('TunableFields')) ids.add('__tunable_fields__')
  for (const m of source.matchAll(/\bid="([^"]+)"/g)) ids.add(m[1])
  for (const m of source.matchAll(/from '@\/components\/([\w./-]+\.vue)'/g)) {
    const child = SOURCES[`/src/components/${m[1]}`]
    expect(child, `no source for /src/components/${m[1]}`).toBeDefined()
    for (const idm of child.matchAll(/\bid="([^"]+)"/g)) ids.add(idm[1])
  }
  return ids
}

describe('ADMIN_SEARCH_INDEX anchors', () => {
  it('names an id the target view actually renders', () => {
    const cache = new Map<string, Set<string>>()
    let checked = 0
    for (const entry of ADMIN_SEARCH_INDEX) {
      if (entry.hash === undefined) continue
      let ids = cache.get(entry.routeName)
      if (!ids) {
        ids = idsRenderedBy(viewSourceFor(entry.routeName))
        cache.set(entry.routeName, ids)
      }
      if (entry.hash.startsWith('#tunable-')) {
        // Rendered dynamically by TunableFields (`:id="\`tunable-${key}\`"`); which
        // page shows which key is pinned by backend/tests/test_admin_search_index_pin.py.
        expect(
          ids.has('__tunable_fields__'),
          `${entry.labelKey}: ${entry.routeName} does not render TunableFields`,
        ).toBe(true)
        continue
      }
      expect(
        ids.has(entry.hash.slice(1)),
        `${entry.labelKey}: ${entry.routeName} renders no id="${entry.hash.slice(1)}"`,
      ).toBe(true)
      checked++
    }
    expect(checked).toBeGreaterThan(10)
  })
})
