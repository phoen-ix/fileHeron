<!-- Route-backed tabs for an admin page whose policy and state share one
     item (see `config/adminNav.ts` - `AdminNavItem.tabs`). Each tab is a real
     route with its own URL, so the historical paths keep working and a tab is
     bookmarkable. The strip is a WAI-ARIA tablist: roving tabindex, arrow /
     Home / End keys move focus and activate. The RouterView the shell renders
     beneath is the tabpanel.

     Do not copy the hand-rolled `.locale-tabs` from AdminSettingsBranding.vue
     for this - those are an in-page state switch with no panel semantics. -->
<template>
  <nav class="admin-tabs" :aria-label="t('admin.tabs_label')">
    <div role="tablist" class="admin-tablist" @keydown="onKeydown">
      <RouterLink
        v-for="(tab, i) in tabs"
        :id="tabId(tab)"
        :key="tab.routeName"
        :ref="(el) => setTabRef(el, i)"
        :to="{ name: tab.routeName }"
        role="tab"
        class="admin-tab"
        :aria-selected="isCurrent(tab)"
        :aria-controls="panelId"
        :tabindex="isCurrent(tab) ? 0 : -1"
        :class="{ 'is-active': isCurrent(tab) }"
      >
        {{ t(tab.labelKey) }}
      </RouterLink>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { type ComponentPublicInstance, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import type { AdminNavTab } from '@/config/adminNav'

const props = defineProps<{
  tabs: AdminNavTab[]
  /** id of the element that holds the tab content (the shell's RouterView wrapper). */
  panelId: string
}>()

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const tabEls = ref<(HTMLElement | null)[]>([])
function setTabRef(el: Element | ComponentPublicInstance | null, i: number) {
  const node = el && '$el' in (el as ComponentPublicInstance) ? (el as ComponentPublicInstance).$el : el
  tabEls.value[i] = (node as HTMLElement | null) ?? null
}

function isCurrent(tab: AdminNavTab): boolean {
  return route.name === tab.routeName
}

function tabId(tab: AdminNavTab): string {
  return `admin-tab-${tab.routeName}`
}

/** Arrow keys move AND activate (automatic activation), per the APG tabs
 *  pattern; Home/End jump to the ends. Focus follows the new route. */
function onKeydown(e: KeyboardEvent) {
  const current = props.tabs.findIndex((tab) => isCurrent(tab))
  if (current < 0) return
  let next: number | null = null
  if (e.key === 'ArrowRight') next = (current + 1) % props.tabs.length
  else if (e.key === 'ArrowLeft') next = (current - 1 + props.tabs.length) % props.tabs.length
  else if (e.key === 'Home') next = 0
  else if (e.key === 'End') next = props.tabs.length - 1
  if (next === null) return
  e.preventDefault()
  const target = props.tabs[next]
  void router.push({ name: target.routeName }).then(() => tabEls.value[next!]?.focus())
}
</script>

<style scoped>
.admin-tablist {
  display: flex;
  gap: var(--fh-space-1);
  border-bottom: 1px solid var(--fh-hairline);
  margin: 0 0 var(--fh-space-4);
  overflow-x: auto;
}

.admin-tab {
  font-family: var(--fh-font-body);
  font-size: var(--fh-text-body-sm);
  color: var(--fh-ink-soft);
  text-decoration: none;
  padding: 0.4rem 0.9rem;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  white-space: nowrap;
  transition:
    color var(--fh-duration-fast) var(--fh-easing),
    border-color var(--fh-duration-fast) var(--fh-easing);
}

.admin-tab:hover {
  color: var(--fh-ink);
}

.admin-tab.is-active {
  color: var(--fh-ink);
  border-bottom-color: var(--fh-accent);
}

.admin-tab:focus-visible {
  outline: 2px solid var(--fh-focus-ring);
  outline-offset: -2px;
  border-radius: var(--fh-radius-sm);
}

@media (prefers-reduced-motion: reduce) {
  .admin-tab {
    transition: none;
  }
}
</style>
