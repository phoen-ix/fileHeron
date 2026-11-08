<script setup lang="ts">
  import { useI18n } from 'vue-i18n'

  import ModalShell from '@/components/ModalShell.vue'

  defineProps<{ open: boolean }>()
  const emit = defineEmits<{ (e: 'close'): void }>()

  const { t } = useI18n()

  interface Row {
    keys: string[]
    labelKey: string
  }

  const rows: Row[] = [
    { keys: ['/'], labelKey: 'shortcuts.row.focus_search' },
    { keys: ['n'], labelKey: 'shortcuts.row.new_share' },
    { keys: ['g', 'i'], labelKey: 'shortcuts.row.goto_inbox' },
    { keys: ['g', 'o'], labelKey: 'shortcuts.row.goto_outbox' },
    { keys: ['g', 'a'], labelKey: 'shortcuts.row.goto_account' },
    { keys: ['?'], labelKey: 'shortcuts.row.toggle_cheatsheet' },
  ]
</script>

<template>
  <ModalShell
    :open="open"
    :label="t('shortcuts.title')"
    :width="460"
    :layer="200"
    @close="emit('close')"
  >
    <h2 class="modal-h2">{{ t('shortcuts.title') }}</h2>
    <p class="hint">{{ t('shortcuts.hint') }}</p>
    <dl class="shortcut-list">
      <template v-for="row in rows" :key="row.keys.join('+')">
        <dt class="keys">
          <kbd v-for="(k, idx) in row.keys" :key="idx" class="fh-kbd">{{ k }}</kbd>
        </dt>
        <dd class="label">{{ t(row.labelKey) }}</dd>
      </template>
    </dl>
    <div class="actions">
      <button type="button" class="fh-btn" @click="emit('close')">
        {{ t('common.close') }}
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

  .hint {
    margin: 0 0 var(--fh-space-3);
    font-size: var(--fh-text-body-sm);
    color: var(--fh-subtle);
  }

  .shortcut-list {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: var(--fh-space-2) var(--fh-space-4);
    margin: 0 0 var(--fh-space-4);
    align-items: center;
  }

  .keys {
    display: inline-flex;
    gap: 4px;
    margin: 0;
  }

  .fh-kbd {
    font-family: var(--fh-font-mono);
    font-size: var(--fh-text-mono-sm);
    background: var(--fh-paper-raised);
    border: 1px solid var(--fh-hairline-strong);
    border-bottom-width: 2px;
    border-radius: 4px;
    padding: 2px 6px;
    color: var(--fh-ink);
    min-width: 1.4em;
    text-align: center;
  }

  .label {
    margin: 0;
    color: var(--fh-ink);
  }

  .actions {
    display: flex;
    justify-content: flex-end;
  }
</style>
