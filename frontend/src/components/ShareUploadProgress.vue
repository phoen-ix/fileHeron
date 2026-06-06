<script setup lang="ts">
/* Dedicated post-"Send" screen: per-file upload progress, the one-time
 * public link (if created), and a timestamped per-file activity log.
 *
 * Purely presentational - the parent (ShareCreate) owns useUpload and feeds
 * everything in via props. Action buttons appear only once nothing is in
 * flight (gated on isActive, NOT allDone, because the 800ms 'finalizing'
 * timer keeps isActive true until the last file truly settles). */
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import UploadFileRow from '@/components/UploadFileRow.vue'
import type { LogEntry, UploadItem } from '@/composables/useUpload'
import type { InlinePublicLinkResult } from '@/types/api'

const props = defineProps<{
  items: UploadItem[]
  publicLink: InlinePublicLinkResult | null
  log: LogEntry[]
  isActive: boolean
  allDone: boolean
  errorCount: number
}>()

defineEmits<{
  retry: [uid: string]
  'view-share': []
  'create-another': []
}>()

const { t, locale } = useI18n()

const headerTitle = computed(() => {
  if (props.isActive) return t('share_create.progress.uploading_title')
  if (props.errorCount > 0)
    return t('share_create.progress.partial_title', { n: props.errorCount })
  return t('share_create.progress.done_title')
})

const plCopied = ref(false)
async function copyPublicLink() {
  if (!props.publicLink) return
  try {
    await navigator.clipboard.writeText(props.publicLink.url)
    plCopied.value = true
    setTimeout(() => (plCopied.value = false), 1600)
  } catch {
    /* clipboard blocked */
  }
}

function formatLogTime(ts: number): string {
  return new Date(ts).toLocaleTimeString(locale.value, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}
</script>

<template>
  <div class="progress-screen">
    <header class="progress-header" :data-active="isActive">
      <span class="fh-eyebrow">{{ t('share_create.progress.uploading_title') }}</span>
      <h2 class="progress-title">{{ headerTitle }}</h2>
    </header>

    <div v-if="publicLink" class="fh-rise plaintext-box">
      <div class="plaintext-eyebrow">{{ t('share_create.public_link.result_eyebrow') }}</div>
      <div class="plaintext-warning">{{ t('share_create.public_link.result_warning') }}</div>
      <pre class="plaintext-token fh-mono">{{ publicLink.url }}</pre>
      <div class="plaintext-actions">
        <button type="button" class="fh-btn-text" @click="copyPublicLink">
          {{ plCopied ? t('api_tokens.copied') : t('api_tokens.copy') }}
        </button>
      </div>
    </div>

    <ul class="file-list">
      <UploadFileRow
        v-for="item in items"
        :key="item.uid"
        :item="item"
        :show-remove="false"
        @retry="$emit('retry', $event)"
      />
    </ul>

    <section class="log-section">
      <span class="fh-eyebrow">{{ t('share_create.progress.log_heading') }}</span>
      <ol class="log-panel fh-mono">
        <li
          v-for="entry in log"
          :key="entry.id"
          class="log-entry"
          :data-kind="entry.kind"
        >
          <time class="log-time">{{ formatLogTime(entry.ts) }}</time>
          <span class="log-file">{{ entry.fileName }}</span>
          <span class="log-msg">{{ t(entry.messageKey, entry.params ?? {}) }}</span>
        </li>
      </ol>
    </section>

    <div v-if="!isActive" class="actions">
      <button type="button" class="fh-btn-text" @click="$emit('create-another')">
        {{ t('share_create.progress.create_another') }}
      </button>
      <button type="button" class="fh-btn" @click="$emit('view-share')">
        {{ t('share_create.progress.view_share') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.progress-screen {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-4);
}

.progress-header {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
}

.progress-title {
  font-family: var(--fh-font-display);
  font-size: var(--fh-text-display-md);
  letter-spacing: -0.01em;
  margin: 0;
}

/* Same one-time-secret framing the create form used for the inline link. */
.plaintext-box {
  padding: var(--fh-space-4);
  background: var(--fh-accent-soft);
  border: var(--fh-border);
  border-left: 2px solid var(--fh-accent);
  border-radius: var(--fh-radius-sm);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.plaintext-eyebrow {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--fh-subtle);
}

.plaintext-warning {
  font-size: var(--fh-text-body-sm);
}

.plaintext-token {
  background: var(--fh-paper);
  padding: var(--fh-space-3);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  font-size: var(--fh-text-mono-md);
  word-break: break-all;
  white-space: pre-wrap;
  margin: 0;
  user-select: all;
}

.plaintext-actions {
  display: flex;
  gap: var(--fh-space-3);
}

.file-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: var(--fh-border);
}

.log-section {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.log-panel {
  list-style: none;
  margin: 0;
  padding: var(--fh-space-3);
  max-height: 14rem;
  overflow-y: auto;
  background: var(--fh-paper-sunk);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
}

.log-entry {
  display: flex;
  gap: var(--fh-space-2);
  font-size: var(--fh-text-mono-sm);
  align-items: baseline;
}

.log-time {
  color: var(--fh-subtle);
  flex-shrink: 0;
}

.log-file {
  color: var(--fh-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 18rem;
  flex-shrink: 0;
}

.log-msg {
  color: var(--fh-subtle);
}

.log-entry[data-kind='done'] .log-msg {
  color: var(--fh-success);
}

.log-entry[data-kind='error'] .log-msg {
  color: var(--fh-danger);
}

.actions {
  display: flex;
  gap: var(--fh-space-4);
  align-items: center;
  justify-content: flex-end;
  padding-top: var(--fh-space-3);
}
</style>
