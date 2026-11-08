<script setup lang="ts">
  /* The one modal chrome: backdrop, dialog role and label, width, stacking
   * layer, and Escape bound on the document (see useEscapeToClose - a key
   * handler on the backdrop never fires).
   *
   * Six files each carried their own scoped `.fh-modal-backdrop` / `.fh-modal`
   * CSS, nine modals their own backdrop markup and eslint disable, and every
   * page its own `useEscapeToClose` call next to a dead `@keydown.escape`
   * (audit 2026-11-08). Content keeps its own classes: a slot renders in the
   * parent's scope, so the parent's scoped styles still apply to it. Attributes
   * (a `data-testid`) land on the dialog box, not the backdrop. */
  import { toRef } from 'vue'

  import { useEscapeToClose } from '@/composables/useEscapeToClose'

  defineOptions({ inheritAttrs: false })

  const props = withDefaults(
    defineProps<{
      open: boolean
      label?: string
      /** Box width in px, capped at 92vw. */
      width?: number
      /** z-index: pages use 100; app-wide dialogs that can open over a page
       *  modal (confirm, step-up) use 300. */
      layer?: number
    }>(),
    { label: '', width: 420, layer: 100 },
  )

  const emit = defineEmits<{ close: [] }>()

  useEscapeToClose(toRef(props, 'open'), () => emit('close'))
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- modal backdrop: click-outside is a convenience, Escape (bound on the document) is the keyboard path -->
  <div v-if="open" class="fh-modal-backdrop" :style="{ zIndex: layer }" @click.self="emit('close')">
    <div
      class="fh-modal"
      role="dialog"
      aria-modal="true"
      :aria-label="label || undefined"
      :style="{ width: `min(${width}px, 92vw)` }"
      v-bind="$attrs"
    >
      <slot />
    </div>
  </div>
</template>

<style scoped>
  .fh-modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(26, 29, 36, 0.4);
    display: grid;
    place-items: center;
  }

  .fh-modal {
    background: var(--fh-paper);
    border: 1px solid var(--fh-hairline-strong);
    box-shadow: 0 8px 40px rgba(26, 29, 36, 0.15);
    padding: var(--fh-space-5);
    max-height: 92vh;
    overflow-y: auto;
  }
</style>
