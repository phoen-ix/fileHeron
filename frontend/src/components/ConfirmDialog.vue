<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const { t } = useI18n()

const confirmBtn = ref<HTMLButtonElement | null>(null)

// Move focus onto the confirm button when the dialog opens so keyboard users
// land inside it (and Enter confirms).
watch(
  () => ui.confirmState?.open,
  async (open) => {
    if (open) {
      await nextTick()
      confirmBtn.value?.focus()
    }
  },
)
</script>

<template>
  <div
    v-if="ui.confirmState"
    class="fh-modal-backdrop"
    role="dialog"
    aria-modal="true"
    :aria-label="ui.confirmState.title || ui.confirmState.message"
    @click.self="ui.resolveConfirm(false)"
    @keydown.esc="ui.resolveConfirm(false)"
  >
    <div class="fh-modal fh-modal--small">
      <h2 v-if="ui.confirmState.title" class="modal-h2">{{ ui.confirmState.title }}</h2>
      <p class="message">{{ ui.confirmState.message }}</p>
      <div class="actions">
        <button type="button" class="fh-btn-ghost fh-btn" @click="ui.resolveConfirm(false)">
          {{ ui.confirmState.cancelLabel || t('common.cancel') }}
        </button>
        <button
          ref="confirmBtn"
          type="button"
          class="fh-btn"
          :class="{ 'fh-btn-danger': ui.confirmState.danger }"
          @click="ui.resolveConfirm(true)"
        >
          {{ ui.confirmState.confirmLabel || t('common.confirm') }}
        </button>
      </div>
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
  z-index: 300;
}

.fh-modal {
  background: var(--fh-paper);
  border: 1px solid var(--fh-hairline-strong);
  box-shadow: 0 8px 40px rgba(26, 29, 36, 0.15);
  padding: var(--fh-space-5);
  width: min(420px, 92vw);
}

.modal-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.25rem;
  margin: 0 0 var(--fh-space-2);
}

.message {
  margin: 0 0 var(--fh-space-4);
  color: var(--fh-ink);
  line-height: 1.5;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--fh-space-3);
}
</style>
