/* Admin sidebar collapse state machine. Three modes (admin account pref,
 * default accordion):
 *  - accordion: at most one category open; opening one closes the others.
 *  - manual:    any number open; each header toggles independently.
 *  - expanded:  NULL-default opens all; toggle behaves like manual.
 *
 * Explicit header toggles persist to the account (synced across devices).
 * Navigation auto-expand is local-only (no PATCH per route change). */

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import * as accountApi from '@/api/account'
import { useApiError } from '@/composables/useApiError'
import {
  ADMIN_CATEGORY_KEYS,
  defaultOpenCategoriesFor,
  routeNameToCategory,
  type AdminNavCategoryKey,
} from '@/config/adminNav'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type { AdminNavCollapseMode } from '@/types/api'

const VALID = new Set<string>(ADMIN_CATEGORY_KEYS)

export function useAdminNavCollapse() {
  const auth = useAuthStore()
  const ui = useUiStore()
  const route = useRoute()
  const { describe } = useApiError()
  const { t } = useI18n()

  const mode = computed<AdminNavCollapseMode>(
    () => auth.user?.admin_nav_collapse_mode ?? 'accordion',
  )

  /** Build the open-set from the persisted value, or the mode default when
   *  nothing is persisted. */
  function seed(): Set<AdminNavCategoryKey> {
    const persisted = auth.user?.admin_nav_open_categories
    if (persisted != null) {
      return new Set(
        persisted.filter((k): k is AdminNavCategoryKey => VALID.has(k)),
      )
    }
    return new Set(defaultOpenCategoriesFor(mode.value))
  }

  const openSet = ref<Set<AdminNavCategoryKey>>(seed())

  const activeCategory = computed<AdminNavCategoryKey | null>(() => {
    const name = route.name
    return (typeof name === 'string' && routeNameToCategory[name]) || null
  })

  function applyAutoExpand() {
    const active = activeCategory.value
    if (!active) return
    if (mode.value === 'accordion') {
      openSet.value = new Set([active])
    } else if (!openSet.value.has(active)) {
      const next = new Set(openSet.value)
      next.add(active)
      openSet.value = next
    }
  }

  // External re-sync: when the persisted value or mode changes (refreshMe,
  // cross-device sync, or a mode switch that resets the set to null), re-seed.
  // Only re-apply auto-expand when nothing is explicitly persisted — otherwise
  // we'd re-open the active category and undo a just-made collapse.
  watch(
    () => [mode.value, auth.user?.admin_nav_open_categories] as const,
    () => {
      openSet.value = seed()
      if (auth.user?.admin_nav_open_categories == null) applyAutoExpand()
    },
  )

  // Navigation auto-expand (local only). Immediate so the initial route opens
  // its category on mount.
  watch(activeCategory, applyAutoExpand, { immediate: true })

  function isOpen(key: AdminNavCategoryKey): boolean {
    return openSet.value.has(key)
  }

  async function toggle(key: AdminNavCategoryKey) {
    const next = new Set(openSet.value)
    if (mode.value === 'accordion') {
      const wasOpen = next.has(key)
      next.clear()
      if (!wasOpen) next.add(key)
    } else if (next.has(key)) {
      next.delete(key)
    } else {
      next.add(key)
    }

    const previous = openSet.value
    openSet.value = next
    try {
      const ordered = ADMIN_CATEGORY_KEYS.filter((k) => next.has(k))
      await accountApi.updateAdminNavOpenCategories(ordered)
      await auth.refreshMe()
    } catch (e) {
      openSet.value = previous
      ui.pushToast(t('admin.nav.save_failed') + ' ' + describe(e), 'error')
    }
  }

  return { mode, isOpen, toggle }
}
