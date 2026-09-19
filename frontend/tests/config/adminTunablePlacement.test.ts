/* The placement map decides which admin page renders each registry tunable
 * (config/adminTunablePlacement.ts). What vitest can check: every route it
 * names exists in the sidebar taxonomy (a placement on a route nobody can
 * reach hides the tunable from every page), the per-key exceptions target a
 * page too, and the fallback lands on the Advanced page. The other half - that
 * the set of tunables the backend serves is exactly what the pages and the
 * search index cover - is pinned from the backend side, which can read the
 * registry (backend/tests/test_admin_search_index_pin.py). */

import { describe, expect, it } from 'vitest'

import { ADMIN_ROUTE_NAMES } from '@/config/adminNav'
import {
  ADVANCED_ROUTE,
  GROUP_PLACEMENT,
  KEY_PLACEMENT,
  placementFor,
} from '@/config/adminTunablePlacement'

describe('adminTunablePlacement', () => {
  it('places every group on a route the sidebar knows', () => {
    for (const [group, route] of Object.entries(GROUP_PLACEMENT)) {
      expect(ADMIN_ROUTE_NAMES.has(route), `${group} → ${route}`).toBe(true)
    }
    expect(Object.keys(GROUP_PLACEMENT).length).toBeGreaterThanOrEqual(10)
  })

  it('places every per-key exception on a route the sidebar knows, or on no TunableFields at all', () => {
    for (const [key, route] of Object.entries(KEY_PLACEMENT)) {
      if (route === null) continue
      expect(ADMIN_ROUTE_NAMES.has(route), `${key} → ${route}`).toBe(true)
    }
  })

  it('leaves only retention and storage on the Advanced page', () => {
    const onAdvanced = Object.entries(GROUP_PLACEMENT)
      .filter(([, route]) => route === ADVANCED_ROUTE)
      .map(([group]) => group)
      .sort()
    expect(onAdvanced).toEqual(['retention', 'storage'])
  })

  it('sends an unknown group to the Advanced page rather than nowhere', () => {
    expect(placementFor('brand_new.key', 'brand_new_group')).toBe(ADVANCED_ROUTE)
  })

  it('lets a key override its group', () => {
    expect(placementFor('public_link.lockout_sec', 'rate_limits')).toBe('admin-settings-public-links')
    expect(placementFor('rate_limit.login', 'rate_limits')).toBe('admin-settings-sign-in')
    expect(placementFor('error_alert.cooldown_minutes', 'error_alert')).toBeNull()
  })
})
