<template>
  <div class="admin-shell" data-density="operator">
    <aside class="admin-sidebar">
      <span class="sidebar-eyebrow">{{ t('admin.eyebrow') }}</span>
      <nav class="sidebar-nav" :aria-label="t('admin.nav_label')">
        <!-- ≤720px: the whole sidebar used to stack above every page, all four
             category headers plus whatever was expanded. Now a horizontal strip
             of category buttons and, beneath it, only the OPEN categories' links
             (accordion mode keeps that to one). Same collapse state machine. -->
        <template v-if="narrow">
          <div class="nav-strip nav-strip-cats">
            <RouterLink
              :to="{ name: ADMIN_OVERVIEW.routeName }"
              class="nav-chip"
              :aria-current="isItemActive(ADMIN_OVERVIEW, route.name) ? 'page' : undefined"
            >
              {{ t(ADMIN_OVERVIEW.labelKey) }}
            </RouterLink>
            <button
              v-for="cat in ADMIN_NAV"
              :key="cat.key"
              type="button"
              class="nav-chip"
              :aria-pressed="isOpen(cat.key)"
              @click="toggle(cat.key)"
            >
              {{ t(cat.labelKey) }}
            </button>
          </div>
          <div v-if="openItems.length" class="nav-strip nav-strip-items">
            <RouterLink
              v-for="item in openItems"
              :key="item.routeName"
              :to="{ name: item.routeName }"
              class="nav-chip nav-chip-link"
              :class="{ 'is-active': isItemActive(item, route.name) }"
              :aria-current="isItemActive(item, route.name) ? 'page' : undefined"
            >
              {{ t(item.labelKey) }}
              <span
                v-if="item.badge === 'inbox_unread' && inboxUnread > 0"
                class="nav-badge"
                >{{ inboxUnread }}</span
              >
            </RouterLink>
          </div>
        </template>
        <!-- Desktop: the Overview link, then the collapsible categories. One
             v-else for the whole branch - a v-if on the Overview link alone
             once captured the categories' v-else and shipped a sidebar with
             nothing but "Overview" (v2.17.1). tests/components/AdminLayout.test.ts
             mounts both branches. -->
        <template v-else>
          <RouterLink
            :to="{ name: ADMIN_OVERVIEW.routeName }"
            class="nav-link nav-link-top"
            :class="{ 'is-active': isItemActive(ADMIN_OVERVIEW, route.name) }"
            :aria-current="isItemActive(ADMIN_OVERVIEW, route.name) ? 'page' : undefined"
          >
            {{ t(ADMIN_OVERVIEW.labelKey) }}
          </RouterLink>
          <div v-for="cat in ADMIN_NAV" :key="cat.key" class="nav-cat">
          <button
            type="button"
            class="nav-cat-header"
            :aria-expanded="isOpen(cat.key)"
            :aria-controls="`nav-cat-${cat.key}`"
            @click="toggle(cat.key)"
          >
            <span>{{ t(cat.labelKey) }}</span>
            <svg
              class="nav-chevron"
              :class="{ open: isOpen(cat.key) }"
              viewBox="0 0 16 16"
              width="12"
              height="12"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              aria-hidden="true"
            >
              <path d="M4 6l4 4 4-4" />
            </svg>
          </button>
          <!-- Collapsed is only a VISUAL state here: the panel animates to
               grid-template-rows 0fr and the inner wrapper clips, but the links
               inside stay in the tab order and in the accessibility tree. A
               keyboard user tabbing past a collapsed category walked through
               every hidden link, and a screen reader announced them, while
               aria-expanded on the header said "collapsed" (audit 2026-07-30,
               fe-i18n-a11y-6). `inert` removes both; aria-hidden is belt and
               braces for anything that does not honour it yet. -->
          <div
            :id="`nav-cat-${cat.key}`"
            class="nav-cat-panel"
            :data-open="isOpen(cat.key)"
            :inert="!isOpen(cat.key)"
            :aria-hidden="!isOpen(cat.key)"
          >
            <div class="nav-cat-panel-inner">
              <RouterLink
                v-for="item in cat.items"
                :key="item.routeName"
                :to="{ name: item.routeName }"
                class="nav-link"
                :class="{ 'is-active': isItemActive(item, route.name) }"
                :aria-current="isItemActive(item, route.name) ? 'page' : undefined"
              >
                {{ t(item.labelKey) }}
                <span
                  v-if="item.badge === 'inbox_unread' && inboxUnread > 0"
                  class="nav-badge"
                  >{{ inboxUnread }}</span
                >
              </RouterLink>
            </div>
          </div>
          </div>
        </template>
      </nav>
    </aside>
    <div class="admin-content">
      <RouterView v-slot="{ Component, route: r }">
        <component :is="Component" :key="adminChildViewKey(r)" />
      </RouterView>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { getInboxUnreadCount } from '@/api/admin'
import { useAdminNavCollapse } from '@/composables/useAdminNavCollapse'
import { ADMIN_NAV, ADMIN_OVERVIEW, type AdminNavItem, isItemActive } from '@/config/adminNav'
import { adminChildViewKey } from '@/utils/viewKeys'

const { t } = useI18n()
const route = useRoute()
const { isOpen, toggle } = useAdminNavCollapse()

// Mirrors the CSS breakpoint below and AppHeader's, where the top nav hides.
const NARROW_QUERY = '(max-width: 720px)'
const narrow = ref(false)
let mq: MediaQueryList | null = null
const onNarrowChange = (e: MediaQueryListEvent | MediaQueryList) => {
  narrow.value = e.matches
}
onMounted(() => {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
  mq = window.matchMedia(NARROW_QUERY)
  onNarrowChange(mq)
  mq.addEventListener('change', onNarrowChange)
})
onBeforeUnmount(() => mq?.removeEventListener('change', onNarrowChange))

/** Links shown under the strip: every open category's items, in nav order. */
const openItems = computed<AdminNavItem[]>(() =>
  ADMIN_NAV.filter((cat) => isOpen(cat.key)).flatMap((cat) => cat.items),
)

// Unread badge on the Inbox nav item (best-effort; silent on failure).
const inboxUnread = ref(0)
async function refreshInboxUnread() {
  try {
    const { data } = await getInboxUnreadCount()
    inboxUnread.value = data.unread
  } catch {
    inboxUnread.value = 0
  }
}
onMounted(refreshInboxUnread)
// The layout now stays mounted across admin pages (utils/viewKeys.ts), so the
// mount-time fetch alone would go stale. The count only changes on the inbox
// pages, so refresh when navigation enters or leaves them - not on every click.
const isInboxRoute = (name: unknown) => String(name ?? '').startsWith('admin-inbox')
watch(
  () => route.name,
  (to, from) => {
    if (isInboxRoute(to) || isInboxRoute(from)) void refreshInboxUnread()
  },
)
</script>

<style scoped>
.admin-shell {
  display: grid;
  grid-template-columns: 200px 1fr;
  min-height: calc(100vh - 60px);
  max-width: var(--fh-max-width-page);
  margin: 0 auto;
  padding: 0 var(--fh-page-gutter);
  gap: var(--fh-space-5);
}

.admin-sidebar {
  padding: var(--fh-space-5) 0;
  border-right: 1px solid var(--fh-hairline);
}

.sidebar-eyebrow {
  display: block;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--fh-subtle);
  margin-bottom: var(--fh-space-3);
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
}

.nav-badge {
  display: inline-block;
  margin-left: 0.4rem;
  min-width: 1.1rem;
  padding: 0 0.3rem;
  border-radius: 0.6rem;
  background: var(--fh-accent);
  color: var(--fh-paper);
  font-size: var(--fh-text-mono-sm);
  font-family: var(--fh-font-mono);
  text-align: center;
  line-height: 1.1rem;
}

.nav-cat {
  display: flex;
  flex-direction: column;
}

.nav-cat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  background: none;
  border: 0;
  cursor: pointer;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--fh-subtle);
  padding: var(--fh-space-2) var(--fh-space-3);
  transition: color var(--fh-duration-fast) var(--fh-easing);
}

.nav-cat-header:hover {
  color: var(--fh-ink);
}

.nav-cat-header:focus-visible {
  outline: 2px solid var(--fh-accent);
  outline-offset: 2px;
}

.nav-chevron {
  flex: none;
  transition: transform var(--fh-duration-fast) var(--fh-easing);
}

.nav-chevron.open {
  transform: rotate(180deg);
}

/* Animate height 0→auto via grid-template-rows interpolation - no JS
 * measuring. The inner wrapper must clip + allow 0 min-height. */
.nav-cat-panel {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows var(--fh-duration) var(--fh-easing);
}

.nav-cat-panel[data-open='true'] {
  grid-template-rows: 1fr;
}

.nav-cat-panel-inner {
  overflow: hidden;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-link {
  font-family: var(--fh-font-body);
  font-size: var(--fh-text-body-md);
  color: var(--fh-ink-soft);
  text-decoration: none;
  padding: var(--fh-space-2) var(--fh-space-3);
  padding-left: var(--fh-space-4);
  border-left: 2px solid transparent;
  transition:
    color var(--fh-duration-fast) var(--fh-easing),
    border-color var(--fh-duration-fast) var(--fh-easing),
    background var(--fh-duration-fast) var(--fh-easing);
}

.nav-link-top {
  padding-left: var(--fh-space-3);
  margin-bottom: var(--fh-space-1);
}

.nav-link:hover {
  color: var(--fh-ink);
  background: var(--fh-paper-raised);
}

.nav-link.is-active {
  color: var(--fh-ink);
  border-left-color: var(--fh-accent);
  background: var(--fh-paper-raised);
}

.admin-content {
  padding: var(--fh-space-4) 0;
}

@media (prefers-reduced-motion: reduce) {
  .nav-cat-panel,
  .nav-chevron {
    transition: none;
  }
}

.nav-strip {
  display: flex;
  gap: var(--fh-space-1);
  overflow-x: auto;
  padding-bottom: var(--fh-space-1);
  scrollbar-width: none;
}

.nav-strip::-webkit-scrollbar {
  display: none;
}

.nav-chip {
  flex: none;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
  background: none;
  border: 1px solid var(--fh-hairline-strong);
  border-radius: var(--fh-radius-sm);
  padding: 5px 10px;
  cursor: pointer;
  text-decoration: none;
}

.nav-chip[aria-pressed='true'] {
  color: var(--fh-paper);
  background: var(--fh-ink);
  border-color: var(--fh-ink);
}

.nav-chip-link {
  font-family: var(--fh-font-body);
  font-size: var(--fh-text-body-sm);
  text-transform: none;
  letter-spacing: 0;
  color: var(--fh-ink-soft);
}

.nav-chip-link.is-active {
  color: var(--fh-ink);
  border-color: var(--fh-accent);
  background: var(--fh-paper-raised);
}

@media (max-width: 720px) {
  .admin-shell {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
  }
  .admin-sidebar {
    min-width: 0;
    border-right: none;
    border-bottom: 1px solid var(--fh-hairline);
    padding: var(--fh-space-2) 0;
  }
  .sidebar-eyebrow {
    margin-bottom: var(--fh-space-2);
  }
}
</style>
