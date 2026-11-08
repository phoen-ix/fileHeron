<script setup lang="ts">
  import { nextTick, ref, watch } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { useUiStore } from '@/stores/ui'
  import ModalShell from '@/components/ModalShell.vue'

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
  <ModalShell
    v-if="ui.confirmState"
    :open="true"
    :label="ui.confirmState.title || ui.confirmState.message"
    :layer="300"
    @close="ui.resolveConfirm(false)"
  >
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
  </ModalShell>
</template>

<style scoped>
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
