/* The navigation-guard decision (extracted from router.beforeEach): the auth
 * gate, the admin role gate, the logged-in-hitting-login bounce, forced-2FA,
 * and the setup-wizard gate. Pure function -> no live router needed. */

import { describe, expect, it } from 'vitest'

import { navigationGuard, type GuardAuthState } from '@/router'
import type { MeResponse } from '@/types/api'
import type { RouteLocationNormalized } from 'vue-router'

function route(opts: {
  name?: string
  path?: string
  fullPath?: string
  meta?: Record<string, unknown>
}): RouteLocationNormalized {
  return {
    name: opts.name,
    path: opts.path ?? '/',
    fullPath: opts.fullPath ?? opts.path ?? '/',
    meta: opts.meta ?? {},
  } as unknown as RouteLocationNormalized
}

function user(over: Partial<MeResponse> = {}): MeResponse {
  return {
    role: 'client',
    requires_2fa: false,
    home_page_enabled: true,
    default_landing_page: null,
    ...over,
  } as MeResponse
}

const anon: GuardAuthState = { setupRequired: false, isAuthenticated: false, user: null }
function authed(over: Partial<MeResponse> = {}): GuardAuthState {
  return { setupRequired: false, isAuthenticated: true, user: user(over) }
}

describe('navigationGuard', () => {
  it('redirects a logged-out user off a protected route to /login with ?redirect', () => {
    const r = navigationGuard(route({ name: 'inbox', path: '/inbox', meta: {} }), anon)
    expect(r).toEqual({ name: 'login', query: { redirect: '/inbox' } })
  })

  it('allows a logged-out user onto a public route', () => {
    const r = navigationGuard(route({ name: 'login', path: '/login', meta: { public: true } }), anon)
    expect(r).toBeUndefined()
  })

  it('forces every route to /setup when setup is required', () => {
    const auth: GuardAuthState = { setupRequired: true, isAuthenticated: false, user: null }
    expect(navigationGuard(route({ name: 'inbox', path: '/inbox' }), auth)).toEqual({ name: 'setup' })
  })

  it('bounces a non-admin off an admin route to their landing', () => {
    const r = navigationGuard(
      route({ name: 'admin-system', path: '/admin/system', meta: { requiresRole: 'admin' } }),
      authed({ role: 'client', home_page_enabled: true }),
    )
    expect(r).toEqual({ path: '/' })
  })

  it('lets an admin onto an admin route', () => {
    const r = navigationGuard(
      route({ name: 'admin-system', path: '/admin/system', meta: { requiresRole: 'admin' } }),
      authed({ role: 'admin' }),
    )
    expect(r).toBeUndefined()
  })

  it('sends a forced-2FA user into the 2FA wizard, carrying the destination', () => {
    const r = navigationGuard(
      route({ name: 'inbox', path: '/inbox', meta: {} }),
      authed({ requires_2fa: true }),
    )
    expect(r).toEqual({ name: 'account-2fa', query: { redirect: '/inbox' } })
  })

  it('does NOT loop a forced-2FA user already on the 2FA route', () => {
    const r = navigationGuard(
      route({ name: 'account-2fa', path: '/account/2fa', meta: {} }),
      authed({ requires_2fa: true }),
    )
    expect(r).toBeUndefined()
  })

  it('bounces a logged-in user off /login to their landing', () => {
    const r = navigationGuard(
      route({ name: 'login', path: '/login', meta: { public: true } }),
      authed({ home_page_enabled: true }),
    )
    expect(r).toEqual({ path: '/' })
  })
})
