<template>
  <li class="file-row" :data-state="item.state">
    <div class="file-row-name" :title="item.file.name">
      <span class="file-name">{{ item.file.name }}</span>
      <span class="file-size">{{ formatBytes(item.file.size) }}</span>
    </div>
    <div class="file-row-progress">
      <div class="bar">
        <div
          class="bar-fill"
          :style="{ width: `${Math.max(2, item.progress)}%` }"
        />
      </div>
      <span class="bar-percent">{{ percentLabel(item) }}</span>
    </div>
    <div class="file-row-status">
      <span class="fh-pill" :data-state="pillState(item.state)">
        {{ t(`upload.state.${item.state}`) }}
      </span>
    </div>
    <div class="file-row-actions">
      <button
        v-if="showRetry && item.state === 'error'"
        type="button"
        class="fh-btn-text"
        @click="$emit('retry', item.uid)"
      >
        {{ t('upload.actions.retry') }}
      </button>
      <button
        v-if="showRemove && canRemove(item.state)"
        type="button"
        class="fh-btn-text danger"
        :disabled="disabled"
        @click="$emit('remove', item.uid)"
      >
        {{ t('upload.actions.remove') }}
      </button>
    </div>
    <!-- A known error code renders as a localized string; an unrecognised one
         falls back to the server's own text, and only then to the generic
         message. The raw English backend string used to go straight to the
         user (audit 2026-07-30, fe-i18n-a11y-5). -->
    <div v-if="item.error || item.errorCode" class="file-row-error">
      {{ errorText }}
    </div>
  </li>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { UploadItem, UploadState } from '@/composables/useUpload'
import { formatBytes } from '@/utils/bytes'

const props = withDefaults(
  defineProps<{
    item: UploadItem
    showRetry?: boolean
    showRemove?: boolean
    disabled?: boolean
  }>(),
  { showRetry: true, showRemove: true, disabled: false },
)

defineEmits<{
  remove: [uid: string]
  retry: [uid: string]
}>()

const { t, te } = useI18n()

// A known error code renders as a localized string; an unrecognised one falls
// back to the server's own text, and only then to the generic message.
const errorText = computed(() => {
  const code = props.item.errorCode
  if (code) {
    const key = `errors.${code}`
    if (te(key)) return t(key)
  }
  return props.item.error ?? t('errors.generic')
})

function canRemove(state: UploadState): boolean {
  return state === 'queued' || state === 'error' || state === 'done'
}

function pillState(state: UploadState): 'active' | 'warn' | 'danger' | undefined {
  if (state === 'done') return 'active'
  if (state === 'finalizing' || state === 'preparing' || state === 'uploading') return 'warn'
  if (state === 'error') return 'danger'
  return undefined
}

function percentLabel(item: UploadItem): string {
  if (item.state === 'done') return '100%'
  if (item.state === 'queued') return '-'
  if (item.state === 'error') return '!'
  return `${item.progress}%`
}

</script>

<style scoped>
.file-row {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(180px, 1fr) auto auto;
  gap: var(--fh-space-3);
  align-items: center;
  padding: var(--fh-space-3) 0;
  border-bottom: var(--fh-border);
}

.file-row-name {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.file-name {
  font-size: var(--fh-text-body-md);
  color: var(--fh-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-size {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.file-row-progress {
  display: flex;
  gap: var(--fh-space-2);
  align-items: center;
}

.bar {
  flex: 1;
  height: 4px;
  background: var(--fh-paper-sunk);
  border-radius: 2px;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background: var(--fh-accent);
  transition: width 200ms var(--fh-easing);
}

.file-row[data-state='done'] .bar-fill {
  background: var(--fh-success);
}

.file-row[data-state='error'] .bar-fill {
  background: var(--fh-danger);
}

.file-row[data-state='queued'] .bar-fill {
  background: var(--fh-hairline-strong);
}

.bar-percent {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
  min-width: 3.5rem;
  text-align: right;
}

.file-row-actions {
  display: flex;
  gap: var(--fh-space-3);
}

.fh-btn-text.danger {
  color: var(--fh-danger);
}
.fh-btn-text.danger:hover {
  color: var(--fh-danger);
  text-decoration-color: var(--fh-danger);
}

.file-row-error {
  grid-column: 1 / -1;
  color: var(--fh-danger);
  font-size: var(--fh-text-body-sm);
  padding-top: var(--fh-space-1);
}

@media (max-width: 720px) {
  .file-row {
    grid-template-columns: 1fr;
    gap: var(--fh-space-1);
  }
}
</style>
