<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import { useI18n } from 'vue-i18n'

import AppHeader from '@/components/AppHeader.vue'
import SiteFooter from '@/components/SiteFooter.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import KeyboardShortcutsModal from '@/components/KeyboardShortcutsModal.vue'
import ToastStack from '@/components/ToastStack.vue'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'
import { setLocale } from '@/i18n'

const auth = useAuthStore()
const site = useSiteStore()
const route = useRoute()
const { t } = useI18n()

const showHeader = computed(() => auth.isAuthenticated && !route.meta.public)
const density = computed(() => route.meta.density ?? 'editorial')
const maintenanceBanner = computed(() =>
  site.maintenance?.enabled ? site.maintenance : null,
)

const { cheatSheetOpen } = useKeyboardShortcuts()

// Move focus to the main region on navigation so keyboard / screen-reader
// users don't stay trapped in the previous page's controls (e.g. a dropdown).
const mainEl = ref<HTMLElement | null>(null)
watch(
  () => route.path,
  async () => {
    await nextTick()
    // Only reset focus if a view hasn't already claimed it (e.g. an autofocused
    // input on a form/login page) - otherwise we'd steal that focus.
    const active = document.activeElement
    if (!active || active === document.body) {
      mainEl.value?.focus({ preventScroll: true })
    }
  },
)

onMounted(() => {
  if (auth.user) setLocale(auth.user.locale)
})

// Maintenance is hydrated once at bootstrap, so an admin who postponed an
// update - which turns maintenance ON from the server side - saw no banner in
// the tab they did it from, and neither did anyone already logged in. The
// banner is the only warning a user gets before their next upload is refused
// (audit 2026-07-30, flow-maintenance-8). Re-read it on navigation, which is
// cheap (one anonymous /api/config-public) and is when the state matters.
watch(
  () => route.fullPath,
  () => {
    void site.loadConfig()
  },
)
</script>

<template>
  <AppHeader v-if="showHeader" />
  <div v-if="maintenanceBanner" class="fh-maintenance-banner" role="status">
    {{ maintenanceBanner.message || t('maintenance.banner_default') }}
  </div>
  <main ref="mainEl" tabindex="-1" :data-density="density">
    <RouterView v-slot="{ Component, route: r }">
      <Transition name="fh-page" mode="out-in">
        <component :is="Component" :key="r.path" />
      </Transition>
    </RouterView>
  </main>
  <SiteFooter />
  <ToastStack />
  <ConfirmDialog />
  <KeyboardShortcutsModal
    :open="cheatSheetOpen"
    @close="cheatSheetOpen = false"
  />
</template>

<style>
/* Programmatic focus target on route change - no visible outline. */
main:focus {
  outline: none;
}
.fh-maintenance-banner {
  background: #fff3cd;
  color: #856404;
  border-bottom: 1px solid #ffeeba;
  padding: 0.6rem 1rem;
  text-align: center;
  font-size: 0.9rem;
}
.fh-page-enter-active,
.fh-page-leave-active {
  transition:
    opacity 220ms cubic-bezier(0.2, 0, 0, 1),
    transform 260ms cubic-bezier(0.2, 0, 0, 1);
}
.fh-page-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
.fh-page-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}
</style>
