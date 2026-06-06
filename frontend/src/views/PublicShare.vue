<script setup lang="ts">
/* /d/:token - anonymous landing page for a public share. No auth, no
 * Pinia. Light editorial framing - this is what a recipient sees,
 * potentially someone less technical than the senders, so the feel
 * leans calm and unsurprising. */
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import {
  fetchPublicShare,
  publicDownloadUrl,
  publicPreviewUrl,
  publicZipUrl,
  unlockPublicShare,
} from '@/api/publicLinks'
import BrandLogo from '@/components/BrandLogo.vue'
import BrandMark from '@/components/BrandMark.vue'
import FilePreviewModal from '@/components/FilePreviewModal.vue'
import { useSiteStore } from '@/stores/site'
import type { PublicShareFile, PublicShareResponse } from '@/types/api'
import { formatBytes } from '@/utils/bytes'
import { formatExpiryInSiteTime } from '@/utils/datetime'
import { previewKind } from '@/utils/preview'

const route = useRoute()
const { t, locale } = useI18n()
const site = useSiteStore()

const showBrand = computed(() => site.branding.show_public)
const showLogo = computed(() => showBrand.value && !!site.branding.logo_url)

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

function formatExpiry(iso: string | null): string {
  return formatExpiryInSiteTime(iso, locale.value, t('expiry.never_label'))
}

function fileEnabled(state: string): boolean {
  return state === 'clean' || state === 'ready_unscanned'
}

// In-browser preview (gated on the global admin switch + a supported type +
// clean state). The cookie-scoped URL is built synchronously - no token mint.
const previewOpen = ref(false)
const previewFile = ref<PublicShareFile | null>(null)
const previewUrl = ref<string | null>(null)

function canPreview(f: PublicShareFile): boolean {
  return (
    !!share.value?.preview_enabled &&
    f.state === 'clean' &&
    previewKind(f.mime_type) !== null
  )
}

function openPreview(f: PublicShareFile) {
  previewFile.value = f
  previewUrl.value = publicPreviewUrl(token.value, f.id)
  previewOpen.value = true
}

function closePreview() {
  previewOpen.value = false
  previewFile.value = null
  previewUrl.value = null
}

function onPreviewDownload() {
  if (previewFile.value) {
    window.location.href = publicDownloadUrl(token.value, previewFile.value.id)
  }
}

// Bulk-ZIP includes only `clean` files; offer it when there's ≥1 and the
// link's download budget isn't spent.
const canDownloadZip = computed(
  () =>
    !!share.value &&
    share.value.files.some((f) => f.state === 'clean') &&
    (share.value.downloads_remaining === null || share.value.downloads_remaining > 0),
)

onMounted(load)
</script>

<template>
  <div class="fh-prose public-share">
    <div v-if="showBrand" class="public-brand">
      <BrandLogo
        v-if="showLogo"
        :src="site.branding.logo_url as string"
        :alt="site.appName"
        :link-url="site.branding.link_url"
        size="sm"
      />
      <BrandMark size="sm" :linkable="false" />
    </div>

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
        <span class="fh-kv-value fh-mono">{{ formatExpiry(share.expires_at) }}</span>
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

        <a
          v-if="canDownloadZip"
          :href="publicZipUrl(token)"
          class="fh-btn-text zip-all"
          :download="`share-${token.slice(0, 8)}.zip`"
        >
          {{ t('public_share.download_all_zip') }} <span aria-hidden="true">↓</span>
        </a>

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
              <button
                v-if="canPreview(f)"
                type="button"
                class="fh-btn-text"
                @click="openPreview(f)"
              >
                {{ t('file_preview.open') }}
              </button>
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

    <FilePreviewModal
      :open="previewOpen"
      :file="previewFile"
      :url="previewUrl"
      @close="closePreview"
      @download="onPreviewDownload"
    />
  </div>
</template>

<style scoped>
.public-share {
  max-width: 720px;
  padding-top: var(--fh-space-6);
  padding-bottom: var(--fh-space-6);
}

.public-brand {
  display: flex;
  align-items: center;
  gap: var(--fh-space-2);
  margin-bottom: var(--fh-space-5);
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
