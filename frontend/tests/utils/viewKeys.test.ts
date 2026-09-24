/* App.vue keyed its top-level view on route.path, which for /admin/* is the
 * AdminLayout - so every admin click remounted the sidebar and a tab switch
 * replaced the tab strip mid-keypress. */
import { describe, expect, it } from 'vitest'
import type { RouteLocationNormalizedLoaded } from 'vue-router'

import { adminChildViewKey, appViewKey } from '@/utils/viewKeys'

function route(path: string, matched: string[], params: Record<string, string> = {}) {
  return { path, params, matched: matched.map((p) => ({ path: p })) } as unknown as RouteLocationNormalizedLoaded
}

describe('view keys', () => {
  it('keeps one AdminLayout across admin pages', () => {
    const a = route('/admin/users', ['/admin', '/admin/users'])
    const b = route('/admin/ip-blocks', ['/admin', '/admin/ip-blocks'])
    expect(appViewKey(a)).toBe(appViewKey(b))
  })

  it('still remounts non-admin pages per path', () => {
    expect(appViewKey(route('/shares/1', ['/shares/:id'], { id: '1' }))).not.toBe(
      appViewKey(route('/shares/2', ['/shares/:id'], { id: '2' })),
    )
  })

  it('keeps a tab shell mounted across its tabs', () => {
    const policy = route('/admin/settings/sessions', ['/admin', '/admin/sessions', '/admin/settings/sessions'])
    const state = route('/admin/sessions', ['/admin', '/admin/sessions', '/admin/sessions'])
    expect(adminChildViewKey(policy)).toBe(adminChildViewKey(state))
  })

  it('remounts a detail page for a different id, as the path key used to', () => {
    const one = route('/admin/users/1', ['/admin', '/admin/users/:id'], { id: '1' })
    const two = route('/admin/users/2', ['/admin', '/admin/users/:id'], { id: '2' })
    expect(adminChildViewKey(one)).not.toBe(adminChildViewKey(two))
  })
})
