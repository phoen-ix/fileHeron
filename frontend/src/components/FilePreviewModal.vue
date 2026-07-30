<script setup lang="ts">
/* In-browser file preview lightbox. Renders by kind:
 *  - image → <img src=…>          (browser streams it)
 *  - pdf   → <iframe src=…>        (browser-native PDF viewer)
 *  - text  → fetched as text into a <pre> (capped by size)
 * `url` is the inline preview URL already authorised for the caller (a `?dt=`
 * token for the authed view, or the path-scoped unlock cookie for the public
 * view). The bytes are served by the backend with a safe Content-Type +
 * nosniff/CSP hardening; this component never renders user HTML. */
import axios from 'axios'
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { previewKind, TEXT_PREVIEW_MAX_BYTES, type PreviewKind } from '@/utils/preview'

interface PreviewFile {
  original_filename: string
  mime_type: string
  size_bytes: number
}

const props = defineProps<{
  open: boolean
  file: PreviewFile | null
  url: string | null
}>()

const emit = defineEmits<{ close: []; download: [] }>()

const { t } = useI18n()
const closeBtn = ref<HTMLButtonElement | null>(null)

const kind = computed<PreviewKind | null>(() =>
  props.file ? previewKind(props.file.mime_type) : null,
)

const textContent = ref('')
const textLoading = ref(false)
const textError = ref(false)
const textTooLarge = computed(
  () => !!props.file && props.file.size_bytes > TEXT_PREVIEW_MAX_BYTES,
)

async function loadText(url: string) {
  textContent.value = ''
  textError.value = false
  if (textTooLarge.value) return
  textLoading.value = true
  try {
    const { data } = await axios.get(url, {
      responseType: 'text',
      withCredentials: true,
    })
    // axios may parse JSON-looking text into an object; coerce back to a string.
    textContent.value =
      typeof data === 'string' ? data : JSON.stringify(data, null, 2)
  } catch {
    textError.value = true
  } finally {
    textLoading.value = false
  }
}

watch(
  () => [props.open, props.url] as const,
  async ([open, url]) => {
    if (open) {
      await nextTick()
      closeBtn.value?.focus()
      if (kind.value === 'text' && url) await loadText(url)
    } else {
      textContent.value = ''
      textError.value = false
    }
  },
)
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- modal backdrop: click-outside is a convenience, Escape is the keyboard path; revisited with the modal focus work -->
  <div
    v-if="open && file"
    class="preview-backdrop"
    role="dialog"
    aria-modal="true"
    :aria-label="file.original_filename"
    @click.self="emit('close')"
    @keydown.esc="emit('close')"
  >
    <div class="preview-modal">
      <header class="preview-head">
        <span class="preview-name fh-mono" :title="file.original_filename">
          {{ file.original_filename }}
        </span>
        <div class="preview-head-actions">
          <button type="button" class="fh-btn-text" @click="emit('download')">
            {{ t('file_preview.download') }} <span aria-hidden="true">↓</span>
          </button>
          <button
            ref="closeBtn"
            type="button"
            class="fh-btn-text"
            @click="emit('close')"
          >
            {{ t('file_preview.close') }} ✕
          </button>
        </div>
      </header>

      <div class="preview-body" :data-kind="kind">
        <div v-if="!url" class="preview-status">{{ t('common.loading') }}</div>

        <img
          v-else-if="kind === 'image'"
          class="preview-image"
          :src="url"
          :alt="file.original_filename"
        />

        <iframe
          v-else-if="kind === 'pdf'"
          class="preview-frame"
          :src="url"
          :title="file.original_filename"
        />

        <template v-else-if="kind === 'text'">
          <div v-if="textTooLarge" class="preview-status">
            {{ t('file_preview.too_large') }}
          </div>
          <div v-else-if="textLoading" class="preview-status">
            {{ t('common.loading') }}
          </div>
          <div v-else-if="textError" class="preview-status">
            {{ t('file_preview.error_loading') }}
          </div>
          <pre v-else class="preview-text">{{ textContent }}</pre>
        </template>

        <div v-else class="preview-status">{{ t('file_preview.not_supported') }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.preview-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(26, 29, 36, 0.55);
  display: grid;
  place-items: center;
  /* Above the confirm dialog (z-index 300). */
  z-index: 301;
  padding: var(--fh-space-4);
}

.preview-modal {
  background: var(--fh-paper-raised);
  border: 1px solid var(--fh-hairline-strong);
  box-shadow: 0 16px 48px rgba(26, 29, 36, 0.2);
  width: min(980px, 94vw);
  max-height: 92vh;
  display: flex;
  flex-direction: column;
}

.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fh-space-3);
  padding: var(--fh-space-3) var(--fh-space-4);
  border-bottom: 1px solid var(--fh-hairline);
}

.preview-name {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-head-actions {
  display: flex;
  gap: var(--fh-space-3);
  flex-shrink: 0;
}

.preview-body {
  min-height: 0;
  flex: 1;
  overflow: auto;
  display: grid;
  place-items: center;
  background: var(--fh-paper-sunk);
}

.preview-status {
  color: var(--fh-subtle);
  padding: var(--fh-space-6);
  text-align: center;
}

.preview-image {
  max-width: 100%;
  max-height: 86vh;
  object-fit: contain;
}

.preview-frame {
  width: 100%;
  height: 80vh;
  border: 0;
  background: var(--fh-paper);
}

.preview-text {
  align-self: stretch;
  justify-self: stretch;
  margin: 0;
  padding: var(--fh-space-4);
  width: 100%;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  line-height: 1.5;
  color: var(--fh-ink);
  white-space: pre-wrap;
  word-break: break-word;
  overflow: auto;
}
</style>
