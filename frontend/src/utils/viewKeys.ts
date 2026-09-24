import type { RouteLocationNormalizedLoaded } from 'vue-router'

/**
 * Keys for the two RouterViews that decide what REMOUNTS on navigation.
 *
 * App.vue keyed its top-level view on `route.path`, and for every /admin/* URL
 * that top-level component is AdminLayout - so each admin click destroyed and
 * re-created the whole sidebar (a fade, plus the inbox-count request again),
 * and a tab switch replaced the tab strip mid-keypress, dropping arrow-key
 * focus onto <body>.
 *
 * The admin layout now keeps one instance, and its own child view is keyed on
 * the child's route record plus params: a different page, or the same detail
 * page for a different id, still remounts (the old path key provided that);
 * switching between the tabs of one page does not, so the tab strip survives.
 */
export function appViewKey(route: RouteLocationNormalizedLoaded): string {
  return route.matched[0]?.path === '/admin' ? '/admin' : route.path
}

export function adminChildViewKey(route: RouteLocationNormalizedLoaded): string {
  const record = route.matched[1]?.path ?? route.path
  return `${record}|${JSON.stringify(route.params)}`
}
