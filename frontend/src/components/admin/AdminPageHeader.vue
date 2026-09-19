<!-- The one page header for every admin view: a clickable breadcrumb
     (Admin › category › page), the page <h1>, an optional back link and an
     optional actions slot.

     The title and the crumb leaf come from the SAME i18n key the sidebar uses
     (`ADMIN_NAV` item.labelKey, resolved from the current route name), so a
     sidebar label and its page title cannot drift - they drifted on six pages
     when each view hand-built its own "Admin / Settings / X" string, in five
     different flavours, none of them clickable.

     Detail pages (a user, a group, an SSO provider, a mail) render their own
     title through the `title` slot or the `title` prop; the crumb leaf then
     becomes a link back to the list page, and `back-to` adds an explicit
     "← Back to <list>" link above the crumbs. -->
<template>
  <header class="admin-page-header">
    <div class="admin-page-header-main">
      <RouterLink v-if="backTo" :to="backTo" class="admin-back">
        ← {{ t('admin.back_to', { page: itemLabel }) }}
      </RouterLink>
      <nav v-if="crumbs.length" class="admin-crumbs" :aria-label="t('admin.breadcrumb_label')">
        <ol>
          <li v-for="(c, i) in crumbs" :key="i">
            <RouterLink v-if="c.to" :to="c.to">{{ c.label }}</RouterLink>
            <span v-else :aria-current="i === crumbs.length - 1 ? 'page' : undefined">{{
              c.label
            }}</span>
          </li>
        </ol>
      </nav>
      <slot v-if="!hideTitle" name="title">
        <h1 class="fh-display admin-page-title">{{ heading }}</h1>
      </slot>
      <slot />
    </div>
    <div v-if="$slots.actions" class="admin-page-header-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed, useSlots } from 'vue'
import { useI18n } from 'vue-i18n'
import { type RouteLocationRaw, useRoute } from 'vue-router'

import { ADMIN_NAV, type AdminNavItem } from '@/config/adminNav'

const props = defineProps<{
  /** Detail-page title (an entity name). Marks the nav item crumb as a link. */
  title?: string
  /** Explicit "← Back to <page>" link, for detail pages. */
  backTo?: RouteLocationRaw
  /** Crumbs and back link only - the view renders its own <h1> further down
   *  (the mail detail pages put the subject inside a header row with actions). */
  hideTitle?: boolean
}>()

const { t } = useI18n()
const route = useRoute()
const slots = useSlots()

interface Crumb {
  label: string
  to?: RouteLocationRaw
}

const match = computed<{ item: AdminNavItem; categoryLabelKey: string } | null>(() => {
  const name = route?.name
  if (typeof name !== 'string') return null
  for (const cat of ADMIN_NAV) {
    const item = cat.items.find((i) => i.matchNames.includes(name))
    if (item) return { item, categoryLabelKey: cat.labelKey }
  }
  return null
})

const itemLabel = computed(() => (match.value ? t(match.value.item.labelKey) : ''))
const isDetail = computed(
  () => props.title !== undefined || Boolean(slots.title) || Boolean(props.hideTitle),
)
const heading = computed(() => props.title ?? itemLabel.value)

const crumbs = computed<Crumb[]>(() => {
  const m = match.value
  if (!m) return []
  const out: Crumb[] = [
    { label: t('admin.eyebrow'), to: '/admin' },
    { label: t(m.categoryLabelKey) },
  ]
  // On a detail page the item crumb links back to the list; on the page itself
  // it is the current leaf.
  out.push(
    isDetail.value
      ? { label: t(m.item.labelKey), to: { name: m.item.routeName } }
      : { label: t(m.item.labelKey) },
  )
  return out
})
</script>

<style scoped>
.admin-page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: var(--fh-space-4);
  margin-bottom: var(--fh-space-3);
}

.admin-page-header-main {
  min-width: 0;
}

.admin-page-header-actions {
  flex: none;
  display: flex;
  gap: var(--fh-space-2);
  align-items: center;
}

.admin-back {
  display: inline-block;
  margin-bottom: var(--fh-space-2);
  color: var(--fh-subtle);
  text-decoration: none;
  font-size: var(--fh-text-body-sm);
}

.admin-back:hover {
  color: var(--fh-accent);
}

.admin-crumbs ol {
  list-style: none;
  margin: 0 0 var(--fh-space-2);
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0 var(--fh-space-1);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--fh-subtle);
}

.admin-crumbs li + li::before {
  content: '›';
  color: var(--fh-hairline-strong);
  margin-right: var(--fh-space-1);
}

.admin-crumbs a {
  color: inherit;
  text-decoration: underline;
  text-decoration-color: var(--fh-hairline-strong);
  text-underline-offset: 3px;
}

.admin-crumbs a:hover {
  color: var(--fh-accent);
  text-decoration-color: var(--fh-accent);
}

.admin-crumbs [aria-current='page'] {
  color: var(--fh-ink);
}

.admin-page-title {
  margin: 0;
}

@media (max-width: 720px) {
  .admin-page-header {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
