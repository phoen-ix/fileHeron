import type { MeResponse } from '@/types/api'

/** Pickable landing route names (mirrors `services/account_prefs.py
 *  ALLOWED_LANDING_ROUTES`). Single source of truth for the SPA. */
export const ALLOWED_LANDING_ROUTES = [
  'home',
  'outbox',
  'inbox',
  'share-create',
  'account',
] as const

export type LandingRouteName = (typeof ALLOWED_LANDING_ROUTES)[number]

/** Map a route name to its URL path. Used to resolve the user's saved
 *  preference into something `router.push()` understands. */
export const ROUTE_NAME_TO_PATH: Record<LandingRouteName, string> = {
  home: '/',
  outbox: '/outbox',
  inbox: '/inbox',
  'share-create': '/share/new',
  account: '/account',
}

/** Resolve the path the user should land on when there's no explicit
 *  redirect intent (no `?redirect=` query param). This is the sole owner
 *  of post-login landing resolution; the backend only validates the saved
 *  value against `services/account_prefs.ALLOWED_LANDING_ROUTES`.
 *
 *  Priority:
 *    1. Saved pref if reachable. ("home" is reachable only when
 *       `home_page_enabled` is true.)
 *    2. Else home if enabled.
 *    3. Else fallback to share-create.
 */
export function effectiveLandingPath(user: MeResponse | null): string {
  if (!user) return '/'

  const pref = user.default_landing_page as LandingRouteName | null
  if (pref) {
    if (pref === 'home') {
      return user.home_page_enabled ? '/' : ROUTE_NAME_TO_PATH['share-create']
    }
    if (ALLOWED_LANDING_ROUTES.includes(pref)) {
      return ROUTE_NAME_TO_PATH[pref]
    }
    // Stale / unknown pref → fall through to defaults.
  }
  if (user.home_page_enabled) return '/'
  return ROUTE_NAME_TO_PATH['share-create']
}
