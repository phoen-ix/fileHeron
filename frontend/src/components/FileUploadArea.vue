<template>
  <div class="upload-area" :class="{ 'has-files': items.length > 0 }">
    <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- drag-and-drop zone; the keyboard path is the sibling file-picker button; revisited with the modal focus work -->
    <label
      class="dropzone"
      :class="{ 'is-dragging': dragging }"
      @dragover.prevent="onDragOver"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
    >
      <input
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
      <UploadFileRow
        v-for="item in items"
        :key="item.uid"
        :item="item"
        :disabled="disabled"
        @retry="$emit('retry', $event)"
        @remove="$emit('remove', $event)"
      />
    </ul>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import UploadFileRow from '@/components/UploadFileRow.vue'
import type { UploadItem } from '@/composables/useUpload'

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
</script>

<style scoped>
.upload-area {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-4);
}

/* Dropzone - editorial framing: hairline rules above and below an
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

/* File list: dense operator-mode grid. Row styling lives in
   UploadFileRow.vue (shared with the upload-progress screen). */
.file-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: var(--fh-border);
}
</style>
