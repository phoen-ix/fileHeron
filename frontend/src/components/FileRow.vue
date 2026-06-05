<template>
  <li class="file-row" :data-state="file.state">
    <div class="meta">
      <div class="filename" :title="file.original_filename">
        {{ file.original_filename }}
      </div>
      <div class="sub">
        <span class="fh-mono size">{{ formatBytes(file.size_bytes) }}</span>
        <span v-if="file.mime_type" class="fh-mono mime">{{ file.mime_type }}</span>
        <span v-if="file.sha256_hex" class="fh-mono sha" :title="file.sha256_hex">
          sha {{ file.sha256_hex.slice(0, 8) }}…
        </span>
      </div>
    </div>
    <div class="state">
      <span class="fh-pill" :data-state="pillForFile(file.state)">
        {{ t(`files.state.${file.state}`) }}
      </span>
    </div>
    <div class="actions">
      <button
        v-if="canDownload(file.state)"
        type="button"
        class="fh-btn-text"
        :disabled="downloading"
        @click="onDownload"
      >
        {{ downloading ? t('common.loading') : t('files.actions.download') }}
      </button>
      <button
        v-if="canDelete"
        type="button"
        class="fh-btn-text danger"
        :disabled="deleting"
        @click="onDelete"
      >
        {{ deleting ? t('common.loading') : t('files.actions.delete') }}
      </button>
    </div>
  </li>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { deleteFile, getDownloadUrl } from '@/api/files'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type { FileInShareResponse, FileState } from '@/types/api'

const props = defineProps<{
  file: FileInShareResponse
  canDelete?: boolean
}>()

const emit = defineEmits<{
  deleted: [fileId: string]
}>()

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()
const deleting = ref(false)
const downloading = ref(false)

async function onDownload() {
  downloading.value = true
  try {
    const { data } = await getDownloadUrl(props.file.id)
    // Browser navigates to the signed URL; server's
    // Content-Disposition forces the download dialog. We don't
    // open a new tab — same-tab navigation lets the browser
    // re-use the existing connection and the page restores when
    // the download dialog appears.
    window.location.href = data.url
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    downloading.value = false
  }
}

function canDownload(state: FileState): boolean {
  return state === 'clean' || state === 'ready_unscanned'
}

function pillForFile(state: FileState): 'active' | 'warn' | 'danger' | undefined {
  if (state === 'clean' || state === 'ready_unscanned') return 'active'
  if (state === 'uploading') return 'warn'
  if (state === 'infected') return 'danger'
  return undefined
}

async function onDelete() {
  if (!(await ui.confirm({ message: t('files.actions.delete_confirm'), danger: true }))) return
  deleting.value = true
  try {
    await deleteFile(props.file.id)
    emit('deleted', props.file.id)
  } finally {
    deleting.value = false
  }
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
.file-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: var(--fh-space-3);
  align-items: center;
  padding: var(--fh-space-3) 0;
  border-bottom: var(--fh-border);
  list-style: none;
}

.meta {
  min-width: 0;
}

.filename {
  font-size: var(--fh-text-body-md);
  color: var(--fh-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sub {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-3);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
  margin-top: 2px;
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
}

.fh-btn-text.danger {
  color: var(--fh-danger);
}

@media (max-width: 720px) {
  .file-row {
    grid-template-columns: 1fr;
    gap: var(--fh-space-1);
  }
}
</style>
