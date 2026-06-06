/* Data-driven admin sidebar taxonomy + helpers. Single source for the
 * category tree, the route→category map (including detail/child routes), and
 * the per-mode default open-set. The category keys MUST stay in sync with the
 * backend's `services/account_prefs.ADMIN_NAV_CATEGORIES`. */

import type { AdminNavCollapseMode } from '@/types/api'

export type AdminNavCategoryKey = 'access' | 'sharing' | 'messaging' | 'system'

export interface AdminNavItem {
  /** Route the link targets. */
  routeName: string
  /** i18n key for the link label. */
  labelKey: string
  /** Route names that light up this item + auto-expand its category — its own
   *  route plus any detail/child routes (which RouterLink can't match alone). */
  matchNames: string[]
}

export interface AdminNavCategory {
  key: AdminNavCategoryKey
  labelKey: string
  items: AdminNavItem[]
}

/** Canonical category order — mirrors
 *  `services/account_prefs.ADMIN_NAV_CATEGORIES_ORDER`. */
export const ADMIN_CATEGORY_KEYS: AdminNavCategoryKey[] = [
  'access',
  'sharing',
  'messaging',
  'system',
]

export const ADMIN_NAV: AdminNavCategory[] = [
  {
    key: 'access',
    labelKey: 'admin.nav_cat.access',
    items: [
      { routeName: 'admin-users', labelKey: 'admin.nav.users', matchNames: ['admin-users', 'admin-user-detail'] },
      { routeName: 'admin-groups', labelKey: 'admin.nav.groups', matchNames: ['admin-groups', 'admin-group-detail'] },
      { routeName: 'admin-sessions', labelKey: 'admin.nav.sessions', matchNames: ['admin-sessions'] },
      { routeName: 'admin-settings-twofa', labelKey: 'admin.nav_item.twofa', matchNames: ['admin-settings-twofa'] },
      { routeName: 'admin-api-tokens', labelKey: 'admin.nav.api_tokens', matchNames: ['admin-api-tokens'] },
      { routeName: 'admin-settings-api-tokens', labelKey: 'admin.nav_item.api_token_policy', matchNames: ['admin-settings-api-tokens'] },
      {
        routeName: 'admin-settings-sso',
        labelKey: 'admin.nav_item.sso',
        matchNames: ['admin-settings-sso', 'admin-settings-sso-new', 'admin-settings-sso-edit'],
      },
    ],
  },
  {
    key: 'sharing',
    labelKey: 'admin.nav_cat.sharing',
    items: [
      { routeName: 'admin-file-history', labelKey: 'admin.nav.file_history', matchNames: ['admin-file-history'] },
      { routeName: 'admin-quarantine', labelKey: 'admin.nav.quarantine', matchNames: ['admin-quarantine'] },
      { routeName: 'admin-settings-quarantine', labelKey: 'admin.nav_item.quarantine_alerts', matchNames: ['admin-settings-quarantine'] },
      { routeName: 'admin-settings-public-links', labelKey: 'admin.nav_item.public_links', matchNames: ['admin-settings-public-links'] },
      { routeName: 'admin-settings-share-approval', labelKey: 'admin.nav_item.share_approval', matchNames: ['admin-settings-share-approval'] },
    ],
  },
  {
    key: 'messaging',
    labelKey: 'admin.nav_cat.messaging',
    items: [
      { routeName: 'admin-inbox', labelKey: 'admin.nav.inbox', matchNames: ['admin-inbox', 'admin-inbox-detail'] },
      { routeName: 'admin-mail-log', labelKey: 'admin.nav.mail_log', matchNames: ['admin-mail-log', 'admin-mail-detail'] },
      { routeName: 'admin-settings-email', labelKey: 'admin.nav_item.email', matchNames: ['admin-settings-email'] },
      { routeName: 'admin-settings-email-templates', labelKey: 'admin.nav_item.email_templates', matchNames: ['admin-settings-email-templates'] },
      { routeName: 'admin-settings-imap', labelKey: 'admin.nav_item.imap', matchNames: ['admin-settings-imap'] },
      { routeName: 'admin-settings-email-change', labelKey: 'admin.nav_item.email_change', matchNames: ['admin-settings-email-change'] },
    ],
  },
  {
    key: 'system',
    labelKey: 'admin.nav_cat.system',
    items: [
      { routeName: 'admin-analytics', labelKey: 'admin.nav.analytics', matchNames: ['admin-analytics'] },
      { routeName: 'admin-audit', labelKey: 'admin.nav.audit', matchNames: ['admin-audit'] },
      { routeName: 'admin-system', labelKey: 'admin.nav.system', matchNames: ['admin-system'] },
      { routeName: 'admin-settings-webhooks', labelKey: 'admin.nav_item.webhooks', matchNames: ['admin-settings-webhooks'] },
      { routeName: 'admin-settings-general', labelKey: 'admin.nav_item.general', matchNames: ['admin-settings-general'] },
      { routeName: 'admin-settings-advanced', labelKey: 'admin.nav_item.advanced', matchNames: ['admin-settings-advanced'] },
    ],
  },
]

/** Maps every match-name (including detail routes) to its category key. */
export const routeNameToCategory: Record<string, AdminNavCategoryKey> = (() => {
  const map: Record<string, AdminNavCategoryKey> = {}
  for (const cat of ADMIN_NAV) {
    for (const item of cat.items) {
      for (const name of item.matchNames) map[name] = cat.key
    }
  }
  return map
})()

/** The default open-set for a mode when nothing is persisted: expanded opens
 *  all categories; accordion/manual start with none (the active category's
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
