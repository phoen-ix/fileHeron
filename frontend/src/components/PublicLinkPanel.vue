<template>
  <section class="public-link-panel">
    <header class="panel-header">
      <h3>{{ t('public_link.title') }}</h3>
      <span v-if="active" class="fh-pill" data-state="active">
        {{ t('public_link.active') }}
      </span>
    </header>

    <p class="fh-field-help intro">{{ t('public_link.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <div v-else-if="active && !justCreated" class="active-card">
      <div v-if="active.url" class="url-row">
        <span class="kv-label">{{ t('public_link.url_label') }}</span>
        <pre class="url fh-mono">{{ active.url }}</pre>
        <button type="button" class="fh-btn-text" @click="copyActiveUrl">
          {{ copiedTimer ? t('public_link.url_copied') : t('public_link.url_copy') }}
        </button>
      </div>
      <p v-else class="fh-field-help token-note">
        {{ t('public_link.url_legacy_hint') }}
      </p>
      <div class="kvs">
        <div class="kv">
          <span class="kv-label">{{ t('public_link.password') }}</span>
          <span class="kv-value">
            {{ active.has_password ? t('public_link.password_set') : t('public_link.password_none') }}
          </span>
        </div>
        <div class="kv">
          <span class="kv-label">{{ t('public_link.limit') }}</span>
          <span class="kv-value fh-mono">
            {{
              active.download_limit
                ? t('public_link.limit_value', {
                    used: active.download_limit - (active.downloads_remaining ?? 0),
                    total: active.download_limit,
                  })
                : t('public_link.limit_none')
            }}
          </span>
        </div>
        <div class="kv">
          <span class="kv-label">{{ t('public_link.notify') }}</span>
          <span class="kv-value">
            {{ active.notify_on_download ? t('common.yes') : t('common.no') }}
          </span>
        </div>
      </div>
      <div class="actions">
        <button
          type="button"
          class="fh-btn-danger fh-btn"
          :disabled="revoking"
          @click="onRevoke"
        >
          {{ revoking ? t('common.loading') : t('public_link.revoke') }}
        </button>
      </div>
    </div>

    <div v-else-if="justCreated" class="created-box fh-rise">
      <div class="created-eyebrow">{{ t('public_link.just_created') }}</div>
      <p class="warning">{{ t('public_link.url_warning') }}</p>
      <pre class="url fh-mono">{{ justCreated.url }}</pre>
      <div class="actions">
        <button type="button" class="fh-btn-text" @click="copyUrl">
          {{ copiedTimer ? t('public_link.copied') : t('public_link.copy') }}
        </button>
        <button type="button" class="fh-btn-text" @click="dismissJustCreated">
          {{ t('public_link.acknowledged') }}
        </button>
      </div>
    </div>

    <form v-else class="create-form" @submit.prevent="onCreate">
      <label class="fh-field">
        <span class="fh-field-label">{{ t('public_link.password_label') }}</span>
        <input
          v-model="newPassword"
          class="fh-field-input"
          type="password"
          autocomplete="new-password"
          :placeholder="t('public_link.password_placeholder')"
        />
        <span class="fh-field-help">{{ t('public_link.password_help') }}</span>
      </label>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('public_link.limit_label') }}</span>
        <input
          v-model.number="newLimit"
          class="fh-field-input fh-field-mono"
          type="number"
          min="1"
          max="100000"
          :placeholder="t('public_link.limit_placeholder')"
        />
        <span class="fh-field-help">{{ t('public_link.limit_help') }}</span>
      </label>

      <label class="checkbox">
        <input v-model="newNotify" type="checkbox" />
        <span>
          <span class="cb-label">{{ t('public_link.notify_label') }}</span>
          <span class="cb-help">{{ t('public_link.notify_help') }}</span>
        </span>
      </label>

      <div v-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>
      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="creating">
          {{ creating ? t('common.loading') : t('public_link.create_submit') }}
        </button>
      </div>
    </form>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  createPublicLink,
  getPublicLink,
  revokePublicLink,
} from '@/api/publicLinks'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type {
  CreatePublicLinkResponse,
  PublicLinkResponse,
} from '@/types/api'

const props = defineProps<{ shareId: string }>()

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const active = ref<PublicLinkResponse | null>(null)
const justCreated = ref<CreatePublicLinkResponse | null>(null)
const loading = ref(true)
const creating = ref(false)
const revoking = ref(false)
const errorMsg = ref<string | null>(null)
const newPassword = ref('')
const newLimit = ref<number | null>(null)
const newNotify = ref(false)
const copiedTimer = ref<number | null>(null)

async function load() {
  loading.value = true
  try {
    const { data } = await getPublicLink(props.shareId)
    active.value = data
  } catch {
    active.value = null
  } finally {
    loading.value = false
  }
}

async function onCreate() {
  errorMsg.value = null
  creating.value = true
  try {
    const { data } = await createPublicLink(props.shareId, {
      password: newPassword.value || null,
      download_limit: newLimit.value || null,
      notify_on_download: newNotify.value,
    })
    justCreated.value = data
    active.value = {
      id: data.id,
      url: data.url,
      download_limit: data.download_limit,
      downloads_remaining: data.downloads_remaining,
      notify_on_download: data.notify_on_download,
      has_password: data.has_password,
      locked_until: null as string | null,
      revoked_at: null as string | null,
      created_at: data.created_at,
    }
    newPassword.value = ''
    newLimit.value = null
    newNotify.value = false
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    creating.value = false
  }
}

async function onRevoke() {
  if (!active.value) return
  if (!(await ui.confirm({ message: t('public_link.revoke_confirm'), danger: true }))) return
  revoking.value = true
  try {
    await revokePublicLink(props.shareId)
    active.value = null
    justCreated.value = null
    ui.pushToast(t('public_link.revoked_toast'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    revoking.value = false
  }
}

async function copyToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    if (copiedTimer.value) clearTimeout(copiedTimer.value)
    copiedTimer.value = window.setTimeout(() => {
      copiedTimer.value = null
    }, 1600)
  } catch {
    /* clipboard blocked */
  }
}

async function copyUrl() {
  if (justCreated.value) await copyToClipboard(justCreated.value.url)
}

async function copyActiveUrl() {
  if (active.value?.url) await copyToClipboard(active.value.url)
}

function dismissJustCreated() {
  justCreated.value = null
}

onMounted(load)
</script>

<style scoped>
.public-link-panel {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  padding-top: var(--fh-space-4);
  border-top: var(--fh-border);
  margin-top: var(--fh-space-4);
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--fh-space-3);
}

.panel-header h3 {
  margin: 0;
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
}

.intro {
  margin: 0 0 var(--fh-space-2);
  max-width: 60ch;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-3) 0;
}

.active-card,
.create-form {
  background: var(--fh-paper-raised);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-4);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-3);
}

.kvs {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--fh-space-3);
}

.kv {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.kv-label {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--fh-subtle);
}

.kv-value {
  font-size: var(--fh-text-body-md);
  color: var(--fh-ink);
}

.token-note {
  margin: 0;
}

.url-row {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
  background: var(--fh-paper);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-3);
}

.url-row .actions,
.url-row button {
  align-self: flex-start;
}

.created-box {
  background: var(--fh-accent-soft);
  border: var(--fh-border);
  border-left: 2px solid var(--fh-accent);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-4);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.created-eyebrow {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--fh-subtle);
}

.warning {
  color: var(--fh-ink);
  margin: 0;
}

.url {
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

.checkbox {
  display: flex;
  gap: var(--fh-space-2);
  align-items: flex-start;
}

.cb-label {
  display: block;
}

.cb-help {
  display: block;
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-1);
}
</style>
