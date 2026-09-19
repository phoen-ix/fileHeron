/* Unit tests for the admin sidebar taxonomy + helpers. Guards the category
 * set and order, route coverage in BOTH directions (every matchName has a
 * category; every admin route in the router has a sidebar home), the
 * detail-route → category mapping, the tab invariants, and the per-mode
 * defaults. */

import { describe, expect, it } from 'vitest'

import en from '@/i18n/locales/en.json'
import de from '@/i18n/locales/de.json'
import {
  ADMIN_CATEGORY_KEYS,
  ADMIN_NAV,
  ADMIN_OVERVIEW,
  ADMIN_ROUTE_NAMES,
  defaultOpenCategoriesFor,
  findNavItem,
  isItemActive,
  routeNameToCategory,
  type AdminNavItem,
} from '@/config/adminNav'

/* Raw source of the router, for the router ⊆ sidebar pin. `import.meta.glob`
 * is typed by vite/client and resolves under vue-tsc, unlike a bare `?raw`
 * specifier. */
const SOURCES = import.meta.glob('/src/router/index.ts', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

function allItems(): AdminNavItem[] {
  return ADMIN_NAV.flatMap((c) => c.items)
}

function lookup(obj: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>(
    (acc, k) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[k] : undefined),
    obj,
  )
}

describe('ADMIN_NAV taxonomy', () => {
  it('has the six task-based categories in canonical order', () => {
    expect(ADMIN_NAV.map((c) => c.key)).toEqual(ADMIN_CATEGORY_KEYS)
    expect(ADMIN_CATEGORY_KEYS).toEqual(['people', 'sharing', 'email', 'security', 'site', 'system'])
  })

  it('places 30 items distributed 7 / 6 / 6 / 3 / 2 / 6, none over seven', () => {
    // The previous taxonomy had 14 of 32 links under "System" - one appended
    // entry per release. Seven is the ceiling; a new page goes into the
    // category of its task, and a policy + its state page become tabs.
    expect(ADMIN_NAV.map((c) => c.items.length)).toEqual([7, 6, 6, 3, 2, 6])
    expect(allItems()).toHaveLength(30)
    for (const cat of ADMIN_NAV) expect(cat.items.length).toBeLessThanOrEqual(7)
  })

  it('lists each primary route exactly once', () => {
    const routes = allItems().map((i) => i.routeName)
    expect(new Set(routes).size).toBe(routes.length)
  })

  it('maps every detail/child route to the correct category', () => {
    expect(routeNameToCategory['admin-user-detail']).toBe('people')
    expect(routeNameToCategory['admin-group-detail']).toBe('people')
    expect(routeNameToCategory['admin-settings-sso-new']).toBe('people')
    expect(routeNameToCategory['admin-settings-sso-edit']).toBe('people')
    expect(routeNameToCategory['admin-mail-detail']).toBe('email')
    expect(routeNameToCategory['admin-inbox-detail']).toBe('email')
  })

  it('routeNameToCategory covers every matchName', () => {
    for (const item of allItems()) {
      for (const name of item.matchNames) {
        expect(routeNameToCategory[name]).toBeDefined()
      }
    }
  })

  it('the Overview belongs to no category and is not a matchName of any item', () => {
    expect(routeNameToCategory[ADMIN_OVERVIEW.routeName]).toBeUndefined()
    expect(findNavItem(ADMIN_OVERVIEW.routeName)).toEqual({
      category: null,
      item: ADMIN_OVERVIEW,
      tab: null,
    })
  })

  it('exposes every label key in en.json AND de.json', () => {
    const keys = [
      ADMIN_OVERVIEW.labelKey,
      ...ADMIN_NAV.flatMap((cat) => [
        cat.labelKey,
        ...cat.items.flatMap((item) => [item.labelKey, ...(item.tabs ?? []).map((t) => t.labelKey)]),
      ]),
    ]
    for (const key of keys) {
      expect(typeof lookup(en, key), `en ${key}`).toBe('string')
      expect(typeof lookup(de, key), `de ${key}`).toBe('string')
    }
  })
})

describe('tabs', () => {
  const tabbed = allItems().filter((i) => i.tabs)

  it('exist on the merged policy/state pairs', () => {
    expect(tabbed.map((i) => i.routeName).sort()).toEqual(
      [
        'admin-api-tokens',
        'admin-error-log',
        'admin-ip-blocks',
        'admin-quarantine',
        'admin-sessions',
        'admin-settings-sign-in',
      ].sort(),
    )
  })

  it('every tab leaf is in the item matchNames and the first tab is the item route', () => {
    // `route.name` is always the LEAF record's name: a tab leaf missing from
    // matchNames gives a page whose sidebar highlights nothing and whose
    // category never auto-expands.
    for (const item of tabbed) {
      expect(item.tabs!.length).toBeGreaterThanOrEqual(2)
      expect(item.tabs![0].routeName).toBe(item.routeName)
      for (const tab of item.tabs!) expect(item.matchNames).toContain(tab.routeName)
    }
  })

  it('findNavItem resolves a tab leaf to its item and tab', () => {
    const m = findNavItem('admin-settings-scan-guard')
    expect(m?.item.routeName).toBe('admin-ip-blocks')
    expect(m?.tab?.routeName).toBe('admin-settings-scan-guard')
    expect(m?.category?.key).toBe('security')
  })
})

describe('router ⊆ sidebar', () => {
  it('every named admin route in the router is known to the sidebar', () => {
    // The inverse of "every matchName has a category": a new admin route that
    // nobody added to ADMIN_NAV renders with an empty crumb, no <h1> and a dark
    // sidebar. `admin-settings` is the legacy hub redirect and renders nothing.
    const src = SOURCES['/src/router/index.ts']
    expect(src, 'router source').toBeTruthy()
    const names = [...src.matchAll(/name: '(admin-[a-z0-9-]+)'/g)].map((m) => m[1])
    expect(names.length).toBeGreaterThan(30)
    const unknown = names.filter((n) => n !== 'admin-settings' && !ADMIN_ROUTE_NAMES.has(n))
    expect(unknown, 'admin routes with no sidebar home').toEqual([])
  })
})

describe('defaultOpenCategoriesFor', () => {
  it('opens all categories in expanded mode', () => {
    expect(defaultOpenCategoriesFor('expanded')).toEqual(ADMIN_CATEGORY_KEYS)
  })

  it('opens none in accordion / manual mode', () => {
    expect(defaultOpenCategoriesFor('accordion')).toEqual([])
    expect(defaultOpenCategoriesFor('manual')).toEqual([])
  })
})

describe('isItemActive', () => {
  const users = allItems().find((i) => i.routeName === 'admin-users')!

  it('matches the primary route and its detail route', () => {
    expect(isItemActive(users, 'admin-users')).toBe(true)
    expect(isItemActive(users, 'admin-user-detail')).toBe(true)
  })

  it('does not match an unrelated route, symbol, or nullish name', () => {
    expect(isItemActive(users, 'admin-groups')).toBe(false)
    expect(isItemActive(users, Symbol('x'))).toBe(false)
    expect(isItemActive(users, null)).toBe(false)
    expect(isItemActive(users, undefined)).toBe(false)
  })
})
