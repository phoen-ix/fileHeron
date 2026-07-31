<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { useI18n } from 'vue-i18n'

import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const { toasts } = storeToRefs(ui)
// The dismiss button's only accessible name was the hardcoded English
// "Dismiss", so a German screen-reader user got one English word in the middle
// of a German page (audit 2026-07-30, fe-i18n-a11y-14). The visible glyph is a
// multiplication sign, which announces as nothing useful on its own.
const { t } = useI18n()
</script>

<template>
  <Teleport to="body">
    <div class="toasts" aria-live="polite" aria-atomic="true">
      <TransitionGroup name="toast">
        <!-- `toast`, not `t`: the loop variable shadowed the i18n `t`, which
             is why the dismiss label could not be translated in place. -->
        <div
          v-for="toast in toasts"
          :key="toast.id"
          class="toast"
          :data-tone="toast.tone"
          role="status"
        >
          <span>{{ toast.text }}</span>
          <button
            class="toast-x"
            type="button"
            :aria-label="t('common.dismiss')"
            @click="ui.dismiss(toast.id)"
          >
            ×
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.toasts {
  position: fixed;
  bottom: var(--fh-space-4);
  right: var(--fh-space-4);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  z-index: 100;
  pointer-events: none;
}

.toast {
  pointer-events: auto;
  background: var(--fh-paper-raised);
  border: 1px solid var(--fh-hairline-strong);
  border-left-width: 2px;
  padding: var(--fh-space-2) var(--fh-space-3);
  font-size: var(--fh-text-body-sm);
  color: var(--fh-ink);
  display: flex;
  align-items: center;
  gap: var(--fh-space-3);
  min-width: 260px;
  max-width: 380px;
}

.toast[data-tone='success'] {
  border-left-color: var(--fh-success);
}
.toast[data-tone='error'] {
  border-left-color: var(--fh-danger);
  color: var(--fh-danger);
}
.toast[data-tone='warn'] {
  border-left-color: var(--fh-warning);
}

.toast-x {
  margin-left: auto;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 18px;
  color: var(--fh-subtle);
  line-height: 1;
}

.toast-x:hover {
  color: var(--fh-ink);
}

.toast-enter-active,
.toast-leave-active {
  transition:
    opacity 220ms var(--fh-easing),
    transform 220ms var(--fh-easing);
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
