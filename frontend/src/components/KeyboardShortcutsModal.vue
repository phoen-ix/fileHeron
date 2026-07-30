<script setup lang="ts">
import { useI18n } from 'vue-i18n'

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
  <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events vuejs-accessibility/no-static-element-interactions -- modal backdrop: click-outside is a convenience, Escape is the keyboard path; revisited with the modal focus work -->
  <div
    v-if="open"
    class="fh-modal-backdrop"
    role="dialog"
    aria-modal="true"
    :aria-label="t('shortcuts.title')"
    @click.self="emit('close')"
  >
    <div class="fh-modal fh-modal--small">
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
  z-index: 200;
}

.fh-modal {
  background: var(--fh-paper);
  border: 1px solid var(--fh-hairline-strong);
  box-shadow: 0 8px 40px rgba(26, 29, 36, 0.15);
  padding: var(--fh-space-5);
  width: min(460px, 92vw);
}

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
