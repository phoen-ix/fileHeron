<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import AppHeader from '@/components/AppHeader.vue'
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

onMounted(() => {
  if (auth.user) setLocale(auth.user.locale)
})
</script>

<template>
  <AppHeader v-if="showHeader" />
  <main :data-density="density">
    <RouterView v-slot="{ Component, route: r }">
      <Transition name="fh-page" mode="out-in">
        <component :is="Component" :key="r.path" />
      </Transition>
    </RouterView>
  </main>
  <ToastStack />
  <KeyboardShortcutsModal
    :open="cheatSheetOpen"
    @close="cheatSheetOpen = false"
  />
</template>

<style>
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
