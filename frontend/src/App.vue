<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import AppHeader from '@/components/AppHeader.vue'
import SiteFooter from '@/components/SiteFooter.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import KeyboardShortcutsModal from '@/components/KeyboardShortcutsModal.vue'
import ToastStack from '@/components/ToastStack.vue'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'
import { useAuthStore } from '@/stores/auth'
import { setLocale } from '@/i18n'

const auth = useAuthStore()
const route = useRoute()

const showHeader = computed(() => auth.isAuthenticated && !route.meta.public)
const density = computed(() => route.meta.density ?? 'editorial')

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
</script>

<template>
  <AppHeader v-if="showHeader" />
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
