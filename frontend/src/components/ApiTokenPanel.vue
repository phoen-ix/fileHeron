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

    <p class="fh-notice" data-tone="muted">{{ t('api_tokens.sessions_note') }}</p>

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
      <ExpiryPicker
        v-model="expiresAtLocal"
        :presets="TOKEN_PRESETS"
        :disabled="creatingBusy"
      />
      <span class="fh-field-help">{{ t('api_tokens.expiry_help') }}</span>

      <fieldset class="scopes-field">
        <legend class="fh-field-label">{{ t('api_tokens.scopes_legend') }}</legend>
        <label class="radio-row">
          <input v-model="scopeMode" type="radio" value="full" :disabled="creatingBusy" />
          <span><strong>{{ t('api_tokens.scope_full') }}</strong> - {{ t('api_tokens.scope_full_help') }}</span>
        </label>
        <label class="radio-row">
          <input v-model="scopeMode" type="radio" value="limited" :disabled="creatingBusy" />
          <span><strong>{{ t('api_tokens.scope_limited') }}</strong> - {{ t('api_tokens.scope_limited_help') }}</span>
        </label>
        <div v-if="scopeMode === 'limited'" class="scope-groups">
          <div v-for="grp in TOKEN_SCOPE_GROUPS" :key="grp.group" class="scope-group">
            <span class="scope-group-title">{{ t('api_tokens.scope_group_' + grp.group) }}</span>
            <label v-for="s in grp.scopes" :key="s" class="check">
              <input v-model="selectedScopes" type="checkbox" :value="s" :disabled="creatingBusy" />
              <span>{{ scopeLabel(s) }}</span>
            </label>
          </div>
        </div>
      </fieldset>

      <!-- Re-auth. The token outlives this session and is not revoked by a
           password reset or "sign out other sessions", so creating one asks for
           the password the same way changing it does. -->
      <label class="fh-field">
        <span class="fh-field-label">{{ t('api_tokens.password_label') }}</span>
        <input
          v-model="createPassword"
          class="fh-field-input"
          type="password"
          autocomplete="current-password"
          :placeholder="t('api_tokens.password_placeholder')"
          required
        />
        <span class="fh-field-help">{{ t('api_tokens.password_help') }}</span>
      </label>

      <div class="create-form-actions">
        <button
          type="submit"
          class="fh-btn"
          :disabled="creatingBusy || !newName || !createPassword || (scopeMode === 'limited' && selectedScopes.length === 0)"
        >
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
          <span class="fh-mono last4">…{{ token.last4 }}</span>
          <span class="fh-mono created">{{ t('api_tokens.created_at', { d: formatDate(token.created_at) }) }}</span>
          <span class="fh-mono used">
            {{
              token.last_used_at
                ? t('api_tokens.last_used', { d: formatDate(token.last_used_at) })
                : t('api_tokens.never_used')
            }}
          </span>
          <span class="fh-mono expiry" :class="{ expired: isExpired(token) }">
            {{ expiryLabel(token) }}
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
        <div class="token-scopes">
          <span v-if="token.scopes === null" class="scope-chip full">
            {{ t('api_tokens.scope_full_badge') }}
          </span>
          <span v-for="s in token.scopes || []" v-else :key="s" class="scope-chip">
            {{ scopeLabel(s) }}
          </span>
        </div>
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
import ExpiryPicker from '@/components/ExpiryPicker.vue'
import { useApiError } from '@/composables/useApiError'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import type { ApiTokenListItem, CreateApiTokenResponse } from '@/types/api'
import { defaultTokenExpiryLocal, parseServerDate, siteLocalIsoToUtcIso } from '@/utils/datetime'
import { TOKEN_SCOPE_GROUPS, scopeLabelKey } from '@/utils/tokenScopes'

// Token-appropriate durations; default null → the picker shows "Never" so a
// token stays unlimited unless the user opts into an expiry.
const TOKEN_PRESETS = ['7d', '30d', '90d', '1y', 'never'] as const

/** Shared with AdminApiTokens.vue so the two forms cannot drift apart. */
const DEFAULT_EXPIRY_LOCAL = defaultTokenExpiryLocal

const { t } = useI18n()
const { formatDate } = useSiteDateFormat()
const { describe } = useApiError()
const ui = useUiStore()

const tokens = ref<ApiTokenListItem[]>([])
const canCreate = ref(true)
const loading = ref(false)
const creating = ref(false)
const creatingBusy = ref(false)
const newName = ref('')
// Defaults are now least-privilege. They used to be "never expires" +
// "unrestricted", so the path of least resistance produced a permanent
// full-access credential - and nothing revokes API tokens on password reset or
// "sign out other sessions". Both wide options are still one click away, but
// they have to be chosen.
const expiresAtLocal = ref<string | null>(DEFAULT_EXPIRY_LOCAL())
const scopeMode = ref<'full' | 'limited'>('limited')
const selectedScopes = ref<string[]>([])
// Re-auth for creation - see the API client for why.
const createPassword = ref('')
const plaintext = ref<CreateApiTokenResponse | null>(null)

function scopeLabel(scope: string): string {
  return t(scopeLabelKey(scope))
}
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
    const expiresAt =
      expiresAtLocal.value === null
        ? null
        : siteLocalIsoToUtcIso(expiresAtLocal.value)
    const scopes = scopeMode.value === 'full' ? null : selectedScopes.value
    const { data } = await createToken(
      newName.value,
      expiresAt,
      scopes,
      createPassword.value,
    )
    plaintext.value = data
    creating.value = false
    newName.value = ''
    createPassword.value = ''
    expiresAtLocal.value = DEFAULT_EXPIRY_LOCAL()
    scopeMode.value = 'limited'
    selectedScopes.value = []
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
  createPassword.value = ''
  // Back to the least-privilege defaults the form opens with. This reset to
  // "never expires" + "full access" - the pre-hardening defaults - so the
  // SECOND time the form was opened it offered a permanent unrestricted
  // credential by default, while the first time did not.
  expiresAtLocal.value = DEFAULT_EXPIRY_LOCAL()
  scopeMode.value = 'limited'
  selectedScopes.value = []
  errorMsg.value = null
}

function isExpired(token: ApiTokenListItem): boolean {
  // expires_at is naive UTC; parseServerDate stamps the Z so the comparison
  // isn't shifted by the viewer's timezone (raw `new Date()` reads it as local).
  return token.expires_at !== null && parseServerDate(token.expires_at) <= new Date()
}

function expiryLabel(token: ApiTokenListItem): string {
  if (token.expires_at === null) return t('api_tokens.never_expires')
  const d = formatDate(token.expires_at)
  return isExpired(token)
    ? t('api_tokens.expired_label', { d })
    : t('api_tokens.expires_label', { d })
}

async function onRevoke(id: number) {
  if (!(await ui.confirm({ message: t('api_tokens.revoke_confirm'), danger: true }))) return
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
    /* clipboard blocked - user can still select-and-copy */
  }
}

function dismissPlaintext() {
  plaintext.value = null
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
  font-size: var(--fh-text-display-md);
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

.scopes-field {
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  margin: 0;
}

.radio-row,
.scopes-field .check {
  display: flex;
  align-items: baseline;
  gap: var(--fh-space-2);
  font-size: var(--fh-text-body-sm);
}

.scope-groups {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-4);
  margin-top: var(--fh-space-1);
  padding-left: var(--fh-space-3);
}

.scope-group {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
}

.scope-group-title {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--fh-subtle);
}

.token-scopes {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-1);
}

.scope-chip {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  padding: 0.1rem 0.5rem;
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  background: var(--fh-paper-raised);
  color: var(--fh-ink-soft);
}

.scope-chip.full {
  color: var(--fh-subtle);
  text-transform: uppercase;
  letter-spacing: 0.08em;
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

.token-meta .expiry.expired {
  color: var(--fh-danger);
  text-transform: uppercase;
  letter-spacing: 0.06em;
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
