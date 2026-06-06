<template>
  <div class="admin-shell" data-density="operator">
    <aside class="admin-sidebar">
      <span class="sidebar-eyebrow">{{ t('admin.eyebrow') }}</span>
      <nav class="sidebar-nav" :aria-label="t('admin.nav_label')">
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
          <div
            :id="`nav-cat-${cat.key}`"
            class="nav-cat-panel"
            :data-open="isOpen(cat.key)"
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
                  v-if="item.routeName === 'admin-inbox' && inboxUnread > 0"
                  class="nav-badge"
                  >{{ inboxUnread }}</span
                >
              </RouterLink>
            </div>
          </div>
        </div>
      </nav>
    </aside>
    <div class="admin-content">
      <RouterView />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { getInboxUnreadCount } from '@/api/admin'
import { useAdminNavCollapse } from '@/composables/useAdminNavCollapse'
import { ADMIN_NAV, isItemActive } from '@/config/adminNav'

const { t } = useI18n()
const route = useRoute()
const { isOpen, toggle } = useAdminNavCollapse()

// Unread badge on the Inbox nav item (best-effort; silent on failure).
const inboxUnread = ref(0)
onMounted(async () => {
  try {
    const { data } = await getInboxUnreadCount()
    inboxUnread.value = data.unread
  } catch {
    inboxUnread.value = 0
  }
})
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

/* Animate height 0→auto via grid-template-rows interpolation — no JS
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

@media (max-width: 720px) {
  .admin-shell {
    grid-template-columns: 1fr;
  }
  .admin-sidebar {
    border-right: none;
    border-bottom: 1px solid var(--fh-hairline);
    padding: var(--fh-space-3) 0;
  }
}
</style>
