/* Data-driven admin sidebar taxonomy + helpers. Single source for the
 * category tree, the route→category map (including detail/child routes and
 * tab leaves), the per-mode default open-set, the page header's crumbs and
 * title, and the Overview's category cards. The category keys MUST stay in
 * sync with the backend's `services/account_prefs.ADMIN_NAV_CATEGORIES`
 * (pinned by `backend/tests/test_admin_nav_categories_pin.py`, which reads
 * both files).
 *
 * Grouping is by what the admin is DOING, not by which release added the
 * page: the previous four categories had grown one appended entry per release
 * until "System" held 14 of 32 links. Two rules keep it from regrowing:
 *  - a policy and the state it produces are TABS on one item, never two
 *    siblings a word apart (Quarantine / Quarantine alerts, API tokens /
 *    Token policy, Error log / Error alerts, Scan guard / Blocked sources);
 *  - a new page goes into the category of its task, and no category holds
 *    more than seven items. */

import type { AdminNavCollapseMode } from '@/types/api'

export type AdminNavCategoryKey = 'people' | 'sharing' | 'email' | 'security' | 'site' | 'system'

export interface AdminNavTab {
  /** Leaf route the tab links to. Must also appear in the item's matchNames. */
  routeName: string
  /** i18n key for the tab label. */
  labelKey: string
}

export interface AdminNavItem {
  /** Route the sidebar link targets (the page, or its first tab). */
  routeName: string
  /** i18n key for the link label - and the page title, and the crumb leaf. */
  labelKey: string
  /** Route names that light up this item + auto-expand its category - its own
   *  route plus any detail/child routes and tab leaves (which RouterLink can't
   *  match alone). vue-router's `route.name` is always the LEAF record's name,
   *  so a tab leaf missing here gives a page whose sidebar highlights nothing. */
  matchNames: string[]
  /** Present ⇒ the route is a tab shell (`views/AdminTabShell.vue`) and the
   *  page header renders these as a tablist. */
  tabs?: AdminNavTab[]
  /** Live count badge rendered next to the label. */
  badge?: 'inbox_unread'
}

interface AdminNavCategory {
  key: AdminNavCategoryKey
  labelKey: string
  items: AdminNavItem[]
}

/** Canonical category order - mirrors
 *  `services/account_prefs.ADMIN_NAV_CATEGORIES_ORDER`. */
export const ADMIN_CATEGORY_KEYS: AdminNavCategoryKey[] = [
  'people',
  'sharing',
  'email',
  'security',
  'site',
  'system',
]

/** The landing page at /admin. Sits above the categories in the sidebar and
 *  belongs to none of them. */
export const ADMIN_OVERVIEW: AdminNavItem = {
  routeName: 'admin-overview',
  labelKey: 'admin.nav.overview',
  matchNames: ['admin-overview'],
}

export const ADMIN_NAV: AdminNavCategory[] = [
  {
    key: 'people',
    labelKey: 'admin.nav_cat.people',
    items: [
      { routeName: 'admin-users', labelKey: 'admin.nav.users', matchNames: ['admin-users', 'admin-user-detail'] },
      { routeName: 'admin-groups', labelKey: 'admin.nav.groups', matchNames: ['admin-groups', 'admin-group-detail'] },
      {
        routeName: 'admin-sessions',
        labelKey: 'admin.nav.sessions',
        matchNames: ['admin-sessions', 'admin-settings-sessions'],
        tabs: [
          { routeName: 'admin-sessions', labelKey: 'admin.nav_tab.active' },
          { routeName: 'admin-settings-sessions', labelKey: 'admin.nav_tab.policy' },
        ],
      },
      {
        routeName: 'admin-api-tokens',
        labelKey: 'admin.nav.api_tokens',
        matchNames: ['admin-api-tokens', 'admin-settings-api-tokens'],
        tabs: [
          { routeName: 'admin-api-tokens', labelKey: 'admin.nav_tab.tokens' },
          { routeName: 'admin-settings-api-tokens', labelKey: 'admin.nav_tab.policy' },
        ],
      },
      {
        routeName: 'admin-settings-sso',
        labelKey: 'admin.nav.sso',
        matchNames: ['admin-settings-sso', 'admin-settings-sso-new', 'admin-settings-sso-edit'],
      },
      { routeName: 'admin-settings-twofa', labelKey: 'admin.nav.twofa', matchNames: ['admin-settings-twofa'] },
      // "Rate limits" was a three-field page with a jargon name; the task is
      // "stop password guessing", so lockout, per-address limits and HIBP sit
      // together, beside the other sign-in identifier policy (email change).
      {
        routeName: 'admin-settings-sign-in',
        labelKey: 'admin.nav.sign_in',
        matchNames: ['admin-settings-sign-in', 'admin-settings-email-change'],
        tabs: [
          { routeName: 'admin-settings-sign-in', labelKey: 'admin.nav_tab.passwords' },
          { routeName: 'admin-settings-email-change', labelKey: 'admin.nav_tab.email_change' },
        ],
      },
    ],
  },
  {
    key: 'sharing',
    labelKey: 'admin.nav_cat.sharing',
    items: [
      { routeName: 'admin-file-history', labelKey: 'admin.nav.file_history', matchNames: ['admin-file-history'] },
      {
        routeName: 'admin-quarantine',
        labelKey: 'admin.nav.quarantine',
        matchNames: ['admin-quarantine', 'admin-settings-quarantine'],
        tabs: [
          { routeName: 'admin-quarantine', labelKey: 'admin.nav_tab.files' },
          { routeName: 'admin-settings-quarantine', labelKey: 'admin.nav_tab.alerts_scanner' },
        ],
      },
      { routeName: 'admin-settings-share-approval', labelKey: 'admin.nav.share_approval', matchNames: ['admin-settings-share-approval'] },
      { routeName: 'admin-settings-public-links', labelKey: 'admin.nav.public_links', matchNames: ['admin-settings-public-links'] },
      { routeName: 'admin-settings-transfers', labelKey: 'admin.nav.transfers', matchNames: ['admin-settings-transfers'] },
      { routeName: 'admin-analytics', labelKey: 'admin.nav.analytics', matchNames: ['admin-analytics'] },
    ],
  },
  {
    key: 'email',
    labelKey: 'admin.nav_cat.email',
    items: [
      { routeName: 'admin-inbox', labelKey: 'admin.nav.inbox', matchNames: ['admin-inbox', 'admin-inbox-detail'], badge: 'inbox_unread' },
      { routeName: 'admin-mail-log', labelKey: 'admin.nav.mail_log', matchNames: ['admin-mail-log', 'admin-mail-detail'] },
      { routeName: 'admin-settings-email', labelKey: 'admin.nav.email', matchNames: ['admin-settings-email'] },
      { routeName: 'admin-settings-imap', labelKey: 'admin.nav.imap', matchNames: ['admin-settings-imap'] },
      { routeName: 'admin-settings-email-templates', labelKey: 'admin.nav.email_templates', matchNames: ['admin-settings-email-templates'] },
      { routeName: 'admin-settings-webhooks', labelKey: 'admin.nav.webhooks', matchNames: ['admin-settings-webhooks'] },
    ],
  },
  {
    key: 'security',
    labelKey: 'admin.nav_cat.security',
    items: [
      // The STATE page is the item; the scan guard's POLICY is a tab inside
      // it. In an incident the operator's noun is "block", and the guard
      // ships off - nesting the blocks page under it hid the incident page
      // under a feature most installs never enable.
      {
        routeName: 'admin-ip-blocks',
        labelKey: 'admin.nav.ip_blocks',
        matchNames: ['admin-ip-blocks', 'admin-settings-scan-guard'],
        tabs: [
          { routeName: 'admin-ip-blocks', labelKey: 'admin.nav_tab.blocks' },
          { routeName: 'admin-settings-scan-guard', labelKey: 'admin.nav_tab.scan_guard' },
        ],
      },
      { routeName: 'admin-settings-anomaly', labelKey: 'admin.nav.anomaly', matchNames: ['admin-settings-anomaly'] },
      { routeName: 'admin-audit', labelKey: 'admin.nav.audit', matchNames: ['admin-audit'] },
    ],
  },
  {
    key: 'site',
    labelKey: 'admin.nav_cat.site',
    items: [
      { routeName: 'admin-settings-general', labelKey: 'admin.nav.general', matchNames: ['admin-settings-general'] },
      { routeName: 'admin-settings-branding', labelKey: 'admin.nav.branding', matchNames: ['admin-settings-branding'] },
    ],
  },
  {
    key: 'system',
    labelKey: 'admin.nav_cat.system',
    items: [
      { routeName: 'admin-system', labelKey: 'admin.nav.system', matchNames: ['admin-system'] },
      { routeName: 'admin-scheduled-tasks', labelKey: 'admin.nav.scheduled_tasks', matchNames: ['admin-scheduled-tasks'] },
      // Errors are OPERATIONS, not security: the alerts fire on failed
      // background tasks and 5xx, so they sit beside Scheduled tasks and
      // Status. The log and the alert settings are two tabs of one page.
      {
        routeName: 'admin-error-log',
        labelKey: 'admin.nav.errors',
        matchNames: ['admin-error-log', 'admin-settings-error-alerts'],
        tabs: [
          { routeName: 'admin-error-log', labelKey: 'admin.nav_tab.log' },
          { routeName: 'admin-settings-error-alerts', labelKey: 'admin.nav_tab.alerts' },
        ],
      },
      { routeName: 'admin-settings-maintenance', labelKey: 'admin.nav.maintenance', matchNames: ['admin-settings-maintenance'] },
      { routeName: 'admin-settings-backup', labelKey: 'admin.nav.backup', matchNames: ['admin-settings-backup'] },
      { routeName: 'admin-settings-advanced', labelKey: 'admin.nav.advanced', matchNames: ['admin-settings-advanced'] },
    ],
  },
]

/** Maps every match-name (including detail routes and tab leaves) to its
 *  category key. The Overview has no category and is deliberately absent. */
export const routeNameToCategory: Record<string, AdminNavCategoryKey> = (() => {
  const map: Record<string, AdminNavCategoryKey> = {}
  for (const cat of ADMIN_NAV) {
    for (const item of cat.items) {
      for (const name of item.matchNames) map[name] = cat.key
    }
  }
  return map
})()

/** Every route name the sidebar knows about - pinned against the router so a
 *  new admin route cannot ship without a sidebar home (or a page whose
 *  sidebar highlights nothing). */
export const ADMIN_ROUTE_NAMES: ReadonlySet<string> = new Set([
  ...ADMIN_OVERVIEW.matchNames,
  ...ADMIN_NAV.flatMap((c) => c.items.flatMap((i) => i.matchNames)),
])

export interface AdminNavMatch {
  /** null for the Overview. */
  category: AdminNavCategory | null
  item: AdminNavItem
  /** The tab the current route is, when the item has tabs. */
  tab: AdminNavTab | null
}

/** Resolves the current route name to its sidebar item (and tab). */
export function findNavItem(routeName: string | symbol | null | undefined): AdminNavMatch | null {
  if (typeof routeName !== 'string') return null
  if (ADMIN_OVERVIEW.matchNames.includes(routeName)) {
    return { category: null, item: ADMIN_OVERVIEW, tab: null }
  }
  for (const category of ADMIN_NAV) {
    const item = category.items.find((i) => i.matchNames.includes(routeName))
    if (item) {
      const tab = item.tabs?.find((t) => t.routeName === routeName) ?? null
      return { category, item, tab }
    }
  }
  return null
}

/** The default open-set for a mode when nothing is persisted: expanded opens
 *  all categories; accordion/manual start with none (the active route's
 *  category is then auto-expanded on navigation). */
export function defaultOpenCategoriesFor(
  mode: AdminNavCollapseMode,
): AdminNavCategoryKey[] {
  return mode === 'expanded' ? [...ADMIN_CATEGORY_KEYS] : []
}

/** True when the current route should highlight this item. Tolerates the
 *  symbol/null/undefined shapes that `route.name` can take in templates. */
export function isItemActive(
  item: AdminNavItem,
  routeName: string | symbol | null | undefined,
): boolean {
  return typeof routeName === 'string' && item.matchNames.includes(routeName)
}
