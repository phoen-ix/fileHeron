<script setup lang="ts">
/* /d/:token — anonymous landing page for a public share. No auth, no
 * Pinia. Light editorial framing — this is what a recipient sees,
 * potentially someone less technical than the senders, so the feel
 * leans calm and unsurprising. */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import {
  fetchPublicShare,
  publicDownloadUrl,
  unlockPublicShare,
} from '@/api/publicLinks'
import type { PublicShareResponse } from '@/types/api'

const route = useRoute()
const { t, locale } = useI18n()

const share = ref<PublicShareResponse | null>(null)
const loading = ref(true)
const errorMsg = ref<string | null>(null)
const errorCode = ref<string | null>(null)

const password = ref('')
const unlocking = ref(false)
const unlockError = ref<string | null>(null)

const token = computed(() => String(route.params.token))

async function load() {
  loading.value = true
  errorMsg.value = null
  errorCode.value = null
  try {
    const { data } = await fetchPublicShare(token.value)
    share.value = data
  } catch (err: unknown) {
    interface AxiosLike { response?: { data?: { error?: string; code?: string } } }
    const e = err as AxiosLike
    errorCode.value = e.response?.data?.code ?? null
    errorMsg.value =
      e.response?.data?.error ?? t('public_share.errors.generic')
  } finally {
    loading.value = false
  }
}

async function onUnlock() {
  unlockError.value = null
  unlocking.value = true
  try {
    await unlockPublicShare(token.value, password.value)
    password.value = ''
    await load()
  } catch (err: unknown) {
    interface AxiosLike { response?: { data?: { error?: string; code?: string } } }
    const e = err as AxiosLike
    unlockError.value =
      e.response?.data?.error ?? t('public_share.errors.unlock_failed')
  } finally {
    unlocking.value = false
  }
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(locale.value === 'de' ? 'de-AT' : 'en-US', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
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

function fileEnabled(state: string): boolean {
  return state === 'clean' || state === 'ready_unscanned'
}

onMounted(load)
</script>

<template>
  <div class="fh-prose public-share">
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <div v-else-if="errorMsg" class="error-state">
      <span class="fh-eyebrow">{{ t('public_share.eyebrow') }}</span>
      <h1 class="fh-display-md">
        {{ t(`public_share.errors.${errorCode}`, t('public_share.errors.generic')) }}
      </h1>
      <p class="fh-field-help">{{ errorMsg }}</p>
    </div>

    <template v-else-if="share">
      <span class="fh-eyebrow fh-rise" data-stagger="1">{{ t('public_share.eyebrow') }}</span>
      <h1 class="fh-display fh-rise" data-stagger="2">
        {{ share.subject || t('public_share.no_subject') }}
      </h1>

      <p class="fh-rise expires-line" data-stagger="2">
        <span class="fh-kv-label">{{ t('public_share.expires') }}</span>
        <span class="fh-kv-value fh-mono">{{ formatDate(share.expires_at) }}</span>
      </p>

      <p v-if="share.message" class="message fh-rise" data-stagger="3">
        {{ share.message }}
      </p>

      <hr class="fh-rule" />

      <form
        v-if="share.requires_password && !share.unlocked"
        class="unlock-form fh-rise"
        data-stagger="3"
        @submit.prevent="onUnlock"
      >
        <label class="fh-field">
          <span class="fh-field-label">{{ t('public_share.password_label') }}</span>
          <input
            v-model="password"
            class="fh-field-input"
            type="password"
            autocomplete="off"
            required
            autofocus
          />
        </label>
        <p class="fh-field-help">{{ t('public_share.password_help') }}</p>
        <div v-if="unlockError" class="fh-notice" data-tone="error">{{ unlockError }}</div>
        <button class="fh-btn" :disabled="unlocking || !password">
          {{ unlocking ? t('common.loading') : t('public_share.unlock') }}
        </button>
      </form>

      <div v-else class="files-section fh-rise" data-stagger="3">
        <h2 class="files-h2">
          {{ t('public_share.files_heading', { n: share.files.length }) }}
        </h2>
        <p
          v-if="share.downloads_remaining !== null"
          class="fh-field-help downloads-remaining"
        >
          {{ t('public_share.downloads_remaining', { n: share.downloads_remaining }) }}
        </p>

        <ul class="files">
          <li v-for="f in share.files" :key="f.id" class="file-row" :data-state="f.state">
            <div class="meta">
              <div class="filename" :title="f.original_filename">{{ f.original_filename }}</div>
              <div class="sub fh-mono">
                {{ formatBytes(f.size_bytes) }} · {{ f.mime_type }}
              </div>
            </div>
            <div class="state-cell">
              <span v-if="f.state === 'ready_unscanned'" class="fh-pill" data-state="warn">
                {{ t('public_share.scanning') }}
              </span>
              <span v-else-if="f.state === 'infected'" class="fh-pill" data-state="danger">
                {{ t('public_share.infected') }}
              </span>
            </div>
            <div class="action">
              <a
                v-if="fileEnabled(f.state)"
                :href="publicDownloadUrl(token, f.id)"
                class="fh-btn-text"
                :download="f.original_filename"
              >
                {{ t('public_share.download') }} <span aria-hidden="true">↓</span>
              </a>
            </div>
          </li>
        </ul>
      </div>
    </template>
  </div>
</template>

<style scoped>
.public-share {
  max-width: 720px;
  padding-top: var(--fh-space-6);
  padding-bottom: var(--fh-space-6);
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}

.error-state {
  text-align: center;
  padding: var(--fh-space-6) 0;
}

.expires-line {
  display: inline-flex;
  align-items: baseline;
  gap: var(--fh-space-2);
  color: var(--fh-subtle);
}

.message {
  background: var(--fh-paper-raised);
  border-left: 2px solid var(--fh-hairline-strong);
  padding: var(--fh-space-3) var(--fh-space-4);
  white-space: pre-wrap;
  line-height: 1.6;
  color: var(--fh-ink-soft);
}

.unlock-form {
  max-width: 420px;
}

.files-section {
  margin-top: var(--fh-space-3);
}

.files-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  margin: var(--fh-space-3) 0;
}

.downloads-remaining {
  margin-bottom: var(--fh-space-3);
}

.files {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: var(--fh-border);
}

.file-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: var(--fh-space-3);
  align-items: center;
  padding: var(--fh-space-3) 0;
  border-bottom: var(--fh-border);
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
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
  margin-top: 2px;
}

.action {
  text-align: right;
}

@media (max-width: 720px) {
  .file-row {
    grid-template-columns: 1fr;
  }
  .action {
    text-align: left;
  }
}
</style>
