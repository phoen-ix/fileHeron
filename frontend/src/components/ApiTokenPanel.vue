<template>
  <section class="api-tokens">
    <header class="panel-header">
      <h3>{{ t('api_tokens.title') }}</h3>
      <button
        v-if="!creating && canCreate"
        type="button"
        class="fh-btn-ghost"
        :disabled="loading"
        @click="creating = true"
      >
        {{ t('api_tokens.create_cta') }}
      </button>
    </header>

    <p class="fh-field-help intro">{{ t('api_tokens.intro') }}</p>

    <p
      v-if="!loading && !canCreate && tokens.length === 0"
      class="fh-notice"
      data-tone="muted"
    >
      {{ t('api_tokens.disabled_by_admin') }}
    </p>

    <form v-if="creating" class="create-form" @submit.prevent="onCreate">
      <label class="fh-field">
        <span class="fh-field-label">{{ t('api_tokens.name_label') }}</span>
        <input
          v-model.trim="newName"
          class="fh-field-input"
          type="text"
          autocomplete="off"
          maxlength="120"
          :placeholder="t('api_tokens.name_placeholder')"
          required
        />
      </label>
      <div class="create-form-actions">
        <button type="submit" class="fh-btn" :disabled="creatingBusy || !newName">
          {{ creatingBusy ? t('common.loading') : t('api_tokens.create_submit') }}
        </button>
        <button type="button" class="fh-btn-text" :disabled="creatingBusy" @click="cancelCreate">
          {{ t('common.cancel') }}
        </button>
      </div>
      <div v-if="errorMsg" class="fh-field-error">{{ errorMsg }}</div>
    </form>

    <div v-if="plaintext" class="plaintext-box fh-rise">
      <div class="plaintext-eyebrow">{{ t('api_tokens.plaintext_eyebrow') }}</div>
      <div class="plaintext-warning">{{ t('api_tokens.plaintext_warning') }}</div>
      <pre class="plaintext-token fh-mono">{{ plaintext.plaintext_token }}</pre>
      <div class="plaintext-actions">
        <button type="button" class="fh-btn-text" @click="copyPlaintext">
          {{ copiedTimer ? t('api_tokens.copied') : t('api_tokens.copy') }}
        </button>
        <button type="button" class="fh-btn-text" @click="dismissPlaintext">
          {{ t('api_tokens.acknowledged') }}
        </button>
      </div>
    </div>

    <ul v-if="tokens.length > 0" class="token-list">
      <li v-for="token in tokens" :key="token.id" class="token-row">
        <div class="token-name">{{ token.name }}</div>
        <div class="token-meta">
          <span class="fh-mono last4">fh_{{ token.id }}_…{{ token.last4 }}</span>
          <span class="fh-mono created">{{ t('api_tokens.created_at', { d: formatDate(token.created_at) }) }}</span>
          <span class="fh-mono used">
            {{
              token.last_used_at
                ? t('api_tokens.last_used', { d: formatDate(token.last_used_at) })
                : t('api_tokens.never_used')
            }}
          </span>
        </div>
        <button
          type="button"
          class="fh-btn-text danger"
          :disabled="revoking === token.id"
          @click="onRevoke(token.id)"
        >
          {{ revoking === token.id ? t('common.loading') : t('api_tokens.revoke') }}
        </button>
      </li>
    </ul>
    <p v-else-if="!loading && !creating" class="fh-field-help empty">
      {{ t('api_tokens.empty') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  createToken,
  listTokens,
  revokeToken,
} from '@/api/apiTokens'
import { useApiError } from '@/composables/useApiError'
import type { ApiTokenListItem, CreateApiTokenResponse } from '@/types/api'
import { formatInSiteTime } from '@/utils/datetime'

const { t, locale } = useI18n()
const { describe } = useApiError()

const tokens = ref<ApiTokenListItem[]>([])
const canCreate = ref(true)
const loading = ref(false)
const creating = ref(false)
const creatingBusy = ref(false)
const newName = ref('')
const plaintext = ref<CreateApiTokenResponse | null>(null)
const errorMsg = ref<string | null>(null)
const revoking = ref<number | null>(null)
const copiedTimer = ref<number | null>(null)

async function refresh() {
  loading.value = true
  try {
    const { data } = await listTokens()
    tokens.value = data.items
    canCreate.value = data.can_create
  } finally {
    loading.value = false
  }
}

async function onCreate() {
  errorMsg.value = null
  creatingBusy.value = true
  try {
    const { data } = await createToken(newName.value)
    plaintext.value = data
    creating.value = false
    newName.value = ''
    await refresh()
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    creatingBusy.value = false
  }
}

function cancelCreate() {
  creating.value = false
  newName.value = ''
  errorMsg.value = null
}

async function onRevoke(id: number) {
  if (!confirm(t('api_tokens.revoke_confirm'))) return
  revoking.value = id
  try {
    await revokeToken(id)
    tokens.value = tokens.value.filter((t) => t.id !== id)
  } finally {
    revoking.value = null
  }
}

async function copyPlaintext() {
  if (!plaintext.value) return
  try {
    await navigator.clipboard.writeText(plaintext.value.plaintext_token)
    if (copiedTimer.value) clearTimeout(copiedTimer.value)
    copiedTimer.value = window.setTimeout(() => {
      copiedTimer.value = null
    }, 1600)
  } catch {
    /* clipboard blocked — user can still select-and-copy */
  }
}

function dismissPlaintext() {
  plaintext.value = null
}

function formatDate(iso: string): string {
  return formatInSiteTime(iso, locale.value)
}

onMounted(refresh)
</script>

<style scoped>
.api-tokens {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--fh-space-3);
}

.panel-header h3 {
  margin: 0;
  font-size: var(--fh-text-display-sm, 1.5rem);
  font-family: var(--fh-font-display);
}

.intro {
  margin: 0 0 var(--fh-space-2);
}

.create-form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  padding: var(--fh-space-3);
  background: var(--fh-paper-raised);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
}

.create-form-actions {
  display: flex;
  gap: var(--fh-space-3);
  align-items: center;
}

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
  color: var(--fh-ink);
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

.token-list {
  list-style: none;
  margin: var(--fh-space-3) 0 0;
  padding: 0;
  border-top: var(--fh-border);
}

.token-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) auto;
  gap: var(--fh-space-3);
  align-items: center;
  padding: var(--fh-space-3) 0;
  border-bottom: var(--fh-border);
}

.token-name {
  font-size: var(--fh-text-body-md);
  color: var(--fh-ink);
}

.token-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-3);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.fh-btn-text.danger {
  color: var(--fh-danger);
}

.empty {
  margin: var(--fh-space-3) 0;
}

@media (max-width: 720px) {
  .token-row {
    grid-template-columns: 1fr;
    gap: var(--fh-space-1);
  }
}
</style>
