<!-- The one shell for every tabbed admin page. It renders the page header
     (crumbs + <h1> from the nav item) and the tablist, then the active tab's
     view. The router mounts it at the item's primary path with the tab leaves
     as children - the historical paths as ABSOLUTE child paths (a child path
     starting with "/" is absolute in vue-router 4), so every stored link
     keeps resolving with no redirect. The leaf views render their own content
     only; their headings live here. -->
<template>
  <div class="admin-tab-shell" data-density="operator">
    <AdminPageHeader />
    <AdminTabs v-if="tabs.length" :tabs="tabs" panel-id="admin-tab-panel" />
    <div id="admin-tab-panel" role="tabpanel" :aria-labelledby="activeTabId">
      <RouterView />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import AdminTabs from '@/components/admin/AdminTabs.vue'
import { findNavItem } from '@/config/adminNav'

const route = useRoute()
const match = computed(() => findNavItem(route.name))
const tabs = computed(() => match.value?.item.tabs ?? [])
const activeTabId = computed(() =>
  match.value?.tab ? `admin-tab-${match.value.tab.routeName}` : undefined,
)
</script>
