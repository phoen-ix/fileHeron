<template>
  <div class="upload-area" :class="{ 'has-files': items.length > 0 }">
    <label
      class="dropzone"
      :class="{ 'is-dragging': dragging }"
      @dragover.prevent="onDragOver"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
    >
      <input
        ref="fileInput"
        type="file"
        multiple
        class="dropzone-input"
        :disabled="disabled"
        @change="onFileChange"
      />
      <div class="dropzone-body">
        <div class="dropzone-rule" />
        <div class="dropzone-eyebrow">{{ t('upload.dropzone.eyebrow') }}</div>
        <div class="dropzone-headline">
          <span class="serif">{{ t('upload.dropzone.headline_a') }}</span>
          <span class="dropzone-or">{{ t('upload.dropzone.or') }}</span>
          <span class="dropzone-action">{{ t('upload.dropzone.headline_b') }}</span>
        </div>
        <div class="dropzone-hint">{{ t('upload.dropzone.hint') }}</div>
        <div class="dropzone-rule" />
      </div>
    </label>

    <ul v-if="items.length > 0" class="file-list">
      <li
        v-for="item in items"
        :key="item.uid"
        class="file-row"
        :data-state="item.state"
      >
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
            v-if="item.state === 'error'"
            type="button"
            class="fh-btn-text"
            @click="$emit('retry', item.uid)"
          >
            {{ t('upload.actions.retry') }}
          </button>
          <button
            v-if="canRemove(item.state)"
            type="button"
            class="fh-btn-text danger"
            :disabled="disabled"
            @click="$emit('remove', item.uid)"
          >
            {{ t('upload.actions.remove') }}
          </button>
        </div>
        <div v-if="item.error" class="file-row-error">{{ item.error }}</div>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import type { UploadItem, UploadState } from '@/composables/useUpload'

defineProps<{
  items: UploadItem[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  add: [files: File[]]
  remove: [uid: string]
  retry: [uid: string]
}>()

const { t } = useI18n()
const fileInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)

function onFileChange(e: Event) {
  const target = e.target as HTMLInputElement
  if (!target.files) return
  emit('add', Array.from(target.files))
  // Allow re-selecting the same file after removing it.
  target.value = ''
}

function onDragOver(e: DragEvent) {
  if (!e.dataTransfer) return
  dragging.value = true
}

function onDrop(e: DragEvent) {
  dragging.value = false
  if (!e.dataTransfer?.files?.length) return
  emit('add', Array.from(e.dataTransfer.files))
}

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
  if (item.state === 'queued') return '—'
  if (item.state === 'error') return '!'
  return `${item.progress}%`
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = n / 1024
  let unitIdx = 0
  while (size >= 1024 && unitIdx < units.length - 1) {
    size /= 1024
    unitIdx++
  }
  return `${size.toFixed(size < 10 ? 2 : 1)} ${units[unitIdx]}`
}
</script>

<style scoped>
.upload-area {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-4);
}

/* Dropzone — editorial framing: hairline rules above and below an
   centered serif headline. Becomes a tighter band once files exist. */
.dropzone {
  display: block;
  cursor: pointer;
  padding: var(--fh-space-6) var(--fh-space-4);
  background: var(--fh-paper);
  border: 1px dashed var(--fh-hairline-strong);
  border-radius: var(--fh-radius-sm);
  transition:
    border-color var(--fh-duration-fast) var(--fh-easing),
    background var(--fh-duration-fast) var(--fh-easing);
}

.has-files .dropzone {
  padding: var(--fh-space-4);
}

.dropzone:hover,
.dropzone.is-dragging {
  border-color: var(--fh-accent);
  background: var(--fh-accent-soft);
}

.dropzone-input {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  border: 0;
}

.dropzone-body {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  align-items: center;
  text-align: center;
}

.dropzone-rule {
  width: 32%;
  height: 1px;
  background: linear-gradient(
    to right,
    transparent,
    var(--fh-hairline-strong),
    transparent
  );
}

.dropzone-eyebrow {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.16em;
  color: var(--fh-subtle);
}

.dropzone-headline {
  font-size: var(--fh-text-body-lg, 1.125rem);
  color: var(--fh-ink);
  display: flex;
  gap: var(--fh-space-2);
  align-items: baseline;
  flex-wrap: wrap;
  justify-content: center;
}

.dropzone-headline .serif {
  font-family: var(--fh-font-display);
  font-size: var(--fh-text-display-md);
  letter-spacing: -0.01em;
}

.dropzone-or {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
  text-transform: lowercase;
  letter-spacing: 0.06em;
}

.dropzone-action {
  font-family: var(--fh-font-display);
  font-size: var(--fh-text-display-md);
  letter-spacing: -0.01em;
  color: var(--fh-accent);
  text-decoration: underline;
  text-decoration-color: var(--fh-accent);
  text-underline-offset: 6px;
  text-decoration-thickness: 1px;
}

.dropzone:hover .dropzone-action,
.dropzone.is-dragging .dropzone-action {
  text-decoration-thickness: 2px;
}

.dropzone-hint {
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
}

/* File list: dense operator-mode grid. */
.file-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: var(--fh-border);
}

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
