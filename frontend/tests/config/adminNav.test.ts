/* Unit tests for the admin sidebar taxonomy + helpers. Guards the route
 * coverage, detail-route → category mapping, and the per-mode defaults. */

import { describe, expect, it } from 'vitest'

import en from '@/i18n/locales/en.json'
import {
  ADMIN_CATEGORY_KEYS,
  ADMIN_NAV,
  defaultOpenCategoriesFor,
  isItemActive,
  routeNameToCategory,
  type AdminNavItem,
} from '@/config/adminNav'

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
  it('has the four canonical categories in order', () => {
    expect(ADMIN_NAV.map((c) => c.key)).toEqual(ADMIN_CATEGORY_KEYS)
    expect(ADMIN_CATEGORY_KEYS).toEqual(['access', 'sharing', 'messaging', 'system'])
  })

  it('places 32 items distributed 7 / 5 / 6 / 14', () => {
    expect(ADMIN_NAV.map((c) => c.items.length)).toEqual([7, 5, 6, 14])
    expect(allItems()).toHaveLength(32)
  })

  it('lists each primary route exactly once', () => {
    const routes = allItems().map((i) => i.routeName)
    expect(new Set(routes).size).toBe(routes.length)
  })

  it('maps every detail/child route to the correct category', () => {
    expect(routeNameToCategory['admin-user-detail']).toBe('access')
    expect(routeNameToCategory['admin-group-detail']).toBe('access')
    expect(routeNameToCategory['admin-settings-sso-new']).toBe('access')
    expect(routeNameToCategory['admin-settings-sso-edit']).toBe('access')
    expect(routeNameToCategory['admin-mail-detail']).toBe('messaging')
  })

  it('routeNameToCategory covers every matchName', () => {
    for (const item of allItems()) {
      for (const name of item.matchNames) {
        expect(routeNameToCategory[name]).toBeDefined()
      }
    }
  })

  it('exposes every label key in en.json', () => {
    for (const cat of ADMIN_NAV) {
      expect(typeof lookup(en, cat.labelKey)).toBe('string')
      for (const item of cat.items) {
        expect(typeof lookup(en, item.labelKey)).toBe('string')
      }
    }
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
