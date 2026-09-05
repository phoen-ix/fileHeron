<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  adminCreateApiToken,
  adminDisableApiToken,
  adminListApiTokens,
  adminReactivateApiToken,
  adminRevokeApiToken,
} from '@/api/admin'
import { searchUsers } from '@/api/users'
import ExpiryPicker from '@/components/ExpiryPicker.vue'
import Pager from '@/components/Pager.vue'
import { useApiError } from '@/composables/useApiError'
import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
import { usePaginatedList } from '@/composables/usePaginatedList'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import { defaultTokenExpiryLocal, siteLocalIsoToUtcIso } from '@/utils/datetime'
import { TOKEN_SCOPE_GROUPS, scopeLabelKey } from '@/utils/tokenScopes'
import type {
  AdminApiTokenItem,
  CreateApiTokenResponse,
  TokenStatus,
  UserSearchItem,
} from '@/types/api'

const { t } = useI18n()
const { formatDate } = useSiteDateFormat()
const { describe } = useApiError()
const ui = useUiStore()

const q = ref('')
const status = ref<TokenStatus | ''>('')
const busyTokenId = ref<number | null>(null)

const { items, total, page, pageSize, loading, errorMsg, load } =
  usePaginatedList<AdminApiTokenItem>(({ page, pageSize }) =>
    adminListApiTokens({
      q: q.value || undefined,
      status: status.value || undefined,
      page,
      page_size: pageSize,
    }).then((r) => r.data),
  )

useDebouncedSearch(q, () => {
  page.value = 1
  void load()
})
watch(status, () => {
  page.value = 1
  void load()
})
watch(page, load)

async function onDisable(item: AdminApiTokenItem) {
  busyTokenId.value = item.id
  try {
    const { data } = await adminDisableApiToken(item.id)
    items.value = items.value.map((t) => (t.id === data.id ? data : t))
    ui.pushToast(t('admin_api_tokens.toast.disabled'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    busyTokenId.value = null
  }
}

async function onReactivate(item: AdminApiTokenItem) {
  busyTokenId.value = item.id
  try {
    const { data } = await adminReactivateApiToken(item.id)
    items.value = items.value.map((t) => (t.id === data.id ? data : t))
    ui.pushToast(t('admin_api_tokens.toast.reactivated'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    busyTokenId.value = null
  }
}

async function onRevoke(item: AdminApiTokenItem) {
  if (!(await ui.confirm({ message: t('admin_api_tokens.revoke_confirm'), danger: true }))) return
  busyTokenId.value = item.id
  try {
    await adminRevokeApiToken(item.id)
    await load()
    ui.pushToast(t('admin_api_tokens.toast.revoked'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    busyTokenId.value = null
  }
}

// --- Generate token for user (inline form) ------------------------------

const showCreateForm = ref(false)
const userQuery = ref('')
const userSuggestions = ref<UserSearchItem[]>([])
const selectedUser = ref<UserSearchItem | null>(null)
const newName = ref('')
// Re-auth: this route mints a token for ANY user, so it is the one a stolen
// admin session would reach for.
const adminPassword = ref('')
// Least-privilege, matching ApiTokenPanel.vue. These were "never expires" +
// "unrestricted", so an admin who filled in name + user + password handed out a
// permanent full-access credential acting as that user - and nothing revokes
// API tokens on password reset or "sign out other sessions". The target user
// cannot see or revoke what was minted for them from their own panel either,
// which makes this the stronger case of the two, not the weaker one. Both wide
// options remain one click away; they just have to be chosen.
const tokenExpiresAt = ref<string | null>(defaultTokenExpiryLocal())
const scopeMode = ref<'full' | 'limited'>('limited')
const selectedScopes = ref<string[]>([])
const creating = ref(false)
const createError = ref<string | null>(null)
const TOKEN_PRESETS = ['7d', '30d', '90d', '1y', 'never'] as const

function scopeLabel(scope: string): string {
  return t(scopeLabelKey(scope))
}
const plaintextResult = ref<CreateApiTokenResponse | null>(null)
const copied = ref(false)
let userSearchTimer: ReturnType<typeof setTimeout> | null = null

watch(userQuery, () => {
  if (userSearchTimer) clearTimeout(userSearchTimer)
  if (selectedUser.value && selectedUser.value.display_name === userQuery.value) {
    return
  }
  // The text no longer names the picked user, so the pick is stale. Dropping it
  // here is what makes "type over the box and press Enter" fail closed: it used
  // to submit the PREVIOUS selection, so an admin who typed "ali", clicked
  // Alice, then retyped "Bob" and submitted handed Bob a full-privilege token
  // acting as Alice - with every audit row attributing his actions to her, and
  // nothing on screen naming the owner (audit #2).
  selectedUser.value = null
  if (!userQuery.value || userQuery.value.length < 2) {
    userSuggestions.value = []
    return
  }
  userSearchTimer = setTimeout(async () => {
    try {
      const { data } = await searchUsers(userQuery.value)
      userSuggestions.value = data.items
    } catch {
      userSuggestions.value = []
    }
  }, 200)
})

function pickUser(u: UserSearchItem) {
  selectedUser.value = u
  userQuery.value = u.display_name
  userSuggestions.value = []
}

onBeforeUnmount(() => {
  if (userSearchTimer) clearTimeout(userSearchTimer)
})

function resetCreateForm() {
  showCreateForm.value = false
  userQuery.value = ''
  userSuggestions.value = []
  selectedUser.value = null
  newName.value = ''
  tokenExpiresAt.value = defaultTokenExpiryLocal()
  scopeMode.value = 'limited'
  selectedScopes.value = []
  createError.value = null
}

async function onCreateForUser() {
  if (!selectedUser.value) {
    createError.value = t('admin_api_tokens.no_user_selected')
    return
  }
  creating.value = true
  createError.value = null
  try {
    const { data } = await adminCreateApiToken({
      target_user_id: selectedUser.value.user_id,
      name: newName.value,
      expires_at:
        tokenExpiresAt.value === null
          ? null
          : siteLocalIsoToUtcIso(tokenExpiresAt.value),
      scopes: scopeMode.value === 'full' ? null : selectedScopes.value,
      password: adminPassword.value,
    })
    plaintextResult.value = data
    adminPassword.value = ''
    showCreateForm.value = false
    userQuery.value = ''
    selectedUser.value = null
    newName.value = ''
    tokenExpiresAt.value = defaultTokenExpiryLocal()
    scopeMode.value = 'limited'
    selectedScopes.value = []
    await load()
  } catch (err) {
    createError.value = describe(err)
  } finally {
    creating.value = false
  }
}

async function copyPlaintext() {
  if (!plaintextResult.value) return
  try {
    await navigator.clipboard.writeText(plaintextResult.value.plaintext_token)
    copied.value = true
    setTimeout(() => (copied.value = false), 1600)
  } catch {
    /* clipboard blocked */
  }
}

function dismissPlaintext() {
  plaintextResult.value = null
}


onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <h1 class="fh-eyebrow">{{ t('admin_api_tokens.eyebrow') }}</h1>
      </div>
      <button
        v-if="!showCreateForm"
        type="button"
        class="fh-btn"
        @click="showCreateForm = true"
      >
        {{ t('admin_api_tokens.create_cta') }} <span aria-hidden="true">→</span>
      </button>
    </div>

    <hr class="fh-rule" />

    <!-- Create-on-behalf form -->
    <form
      v-if="showCreateForm"
      class="create-form"
      @submit.prevent="onCreateForUser"
    >
      <h2 class="form-h2">{{ t('admin_api_tokens.create_heading') }}</h2>
      <p class="fh-field-help">{{ t('admin_api_tokens.create_help') }}</p>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_api_tokens.target_user') }}</span>
        <span v-if="selectedUser" class="picked-user fh-mono">
          {{ t('admin_api_tokens.picked_user', { name: selectedUser.display_name, email: selectedUser.email }) }}
        </span>
        <input
          v-model.trim="userQuery"
          type="search"
          class="fh-field-input"
          autocomplete="off"
          :placeholder="t('admin_api_tokens.target_user_placeholder')"
          required
        />
        <ul v-if="userSuggestions.length > 0" class="user-suggestions">
          <li v-for="u in userSuggestions" :key="u.user_id">
            <button type="button" class="user-suggest" @click="pickUser(u)">
              <span class="row-name">{{ u.display_name }}</span>
              <span class="fh-mono row-hint">{{ u.email }} · {{ u.role }}</span>
            </button>
          </li>
        </ul>
      </label>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_api_tokens.token_name') }}</span>
        <input
          v-model.trim="newName"
          type="text"
          class="fh-field-input"
          maxlength="120"
          required
          :placeholder="t('admin_api_tokens.token_name_placeholder')"
        />
      </label>

      <ExpiryPicker
        v-model="tokenExpiresAt"
        :presets="TOKEN_PRESETS"
        :disabled="creating"
      />
      <span class="fh-field-help">{{ t('api_tokens.expiry_help') }}</span>

      <fieldset class="scopes-field">
        <legend class="fh-field-label">{{ t('api_tokens.scopes_legend') }}</legend>
        <label class="radio-row">
          <input v-model="scopeMode" type="radio" value="full" :disabled="creating" />
          <span><strong>{{ t('api_tokens.scope_full') }}</strong> - {{ t('api_tokens.scope_full_help') }}</span>
        </label>
        <label class="radio-row">
          <input v-model="scopeMode" type="radio" value="limited" :disabled="creating" />
          <span><strong>{{ t('api_tokens.scope_limited') }}</strong> - {{ t('api_tokens.scope_limited_help') }}</span>
        </label>
        <div v-if="scopeMode === 'limited'" class="scope-groups">
          <div v-for="grp in TOKEN_SCOPE_GROUPS" :key="grp.group" class="scope-group">
            <span class="scope-group-title">{{ t('api_tokens.scope_group_' + grp.group) }}</span>
            <label v-for="s in grp.scopes" :key="s" class="check">
              <input v-model="selectedScopes" type="checkbox" :value="s" :disabled="creating" />
              <span>{{ scopeLabel(s) }}</span>
            </label>
          </div>
        </div>
      </fieldset>

      <div
v-if="createError" class="fh-notice" role="alert"
        data-tone="error">{{ createError }}</div>

      <!-- Re-auth. Minting a token on someone else's behalf is the strongest
           form of this action, so it is gated like the self-service one. -->
      <label class="fh-field">
        <span class="fh-field-label">{{ t('api_tokens.password_label') }}</span>
        <input
          v-model="adminPassword"
          class="fh-field-input"
          type="password"
          autocomplete="current-password"
          :placeholder="t('api_tokens.password_placeholder')"
          required
        />
      </label>

      <div class="form-actions">
        <button
          type="submit"
          class="fh-btn"
          :disabled="creating || !selectedUser || !newName || !adminPassword || (scopeMode === 'limited' && selectedScopes.length === 0)"
        >
          {{ creating ? t('common.loading') : t('admin_api_tokens.create_submit') }}
        </button>
        <button type="button" class="fh-btn-text" @click="resetCreateForm">
          {{ t('common.cancel') }}
        </button>
      </div>
    </form>

    <!-- Plaintext one-time disclosure -->
    <div v-if="plaintextResult" class="plaintext-box fh-rise">
      <div class="plaintext-eyebrow">{{ t('admin_api_tokens.plaintext_eyebrow') }}</div>
      <div class="plaintext-owner">
        {{ t('admin_api_tokens.plaintext_owner', { name: plaintextResult.owner_display_name || plaintextResult.owner_user_id }) }}
      </div>
      <div class="plaintext-warning">{{ t('admin_api_tokens.plaintext_warning') }}</div>
      <pre class="plaintext-token fh-mono">{{ plaintextResult.plaintext_token }}</pre>
      <div class="plaintext-actions">
        <button type="button" class="fh-btn-text" @click="copyPlaintext">
          {{ copied ? t('api_tokens.copied') : t('api_tokens.copy') }}
        </button>
        <button type="button" class="fh-btn-text" @click="dismissPlaintext">
          {{ t('api_tokens.acknowledged') }}
        </button>
      </div>
    </div>

    <template v-if="!showCreateForm">
      <div class="filters">
        <input
          v-model.trim="q"
          :aria-label="t('admin_api_tokens.search_placeholder')"
          type="search"
          class="fh-field-input search"
          :placeholder="t('admin_api_tokens.search_placeholder')"
        />
        <select
        v-model="status" class="status-select"
        :aria-label="t('common.filter')"
      >
          <option value="">{{ t('admin_api_tokens.status_all') }}</option>
          <option value="active">{{ t('admin_api_tokens.status.active') }}</option>
          <option value="disabled">{{ t('admin_api_tokens.status.disabled') }}</option>
          <option value="expired">{{ t('admin_api_tokens.status.expired') }}</option>
          <option value="revoked">{{ t('admin_api_tokens.status.revoked') }}</option>
        </select>
      </div>

      <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
      <div
v-else-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>

      <table v-else-if="items.length > 0" class="token-table">
        <thead>
          <tr>
            <th>{{ t('admin_api_tokens.col.name') }}</th>
            <th>{{ t('admin_api_tokens.col.owner') }}</th>
            <th>{{ t('admin_api_tokens.col.status') }}</th>
            <th>{{ t('admin_api_tokens.col.last_used') }}</th>
            <th>{{ t('admin_api_tokens.col.created') }}</th>
            <th>{{ t('admin_api_tokens.col.expiry') }}</th>
            <th class="actions-col"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td>
              <div class="row-name">{{ item.name }}</div>
              <div class="fh-mono row-hint">…{{ item.last4 }}</div>
              <div class="token-scopes-cell">
                <span v-if="item.scopes === null" class="scope-chip full">
                  {{ t('api_tokens.scope_full_badge') }}
                </span>
                <span v-for="s in item.scopes || []" v-else :key="s" class="scope-chip">
                  {{ scopeLabel(s) }}
                </span>
              </div>
            </td>
            <td>
              <div class="row-name">{{ item.owner_display_name }}</div>
              <div class="fh-mono row-hint">{{ item.owner_email }} · {{ item.owner_role }}</div>
            </td>
            <td>
              <span class="fh-pill" :data-state="item.status">
                {{ t(`admin_api_tokens.status.${item.status}`) }}
              </span>
            </td>
            <td class="fh-mono">{{ formatDate(item.last_used_at) }}</td>
            <td class="fh-mono">{{ formatDate(item.created_at) }}</td>
            <td class="fh-mono">
              {{ item.expires_at ? formatDate(item.expires_at) : t('admin_api_tokens.never_expires') }}
            </td>
            <td class="actions">
              <button
                v-if="item.status === 'active'"
                type="button"
                class="fh-btn-text"
                :disabled="busyTokenId === item.id"
                @click="onDisable(item)"
              >
                {{ t('admin_api_tokens.action.disable') }}
              </button>
              <button
                v-if="item.status === 'disabled'"
                type="button"
                class="fh-btn-text"
                :disabled="busyTokenId === item.id"
                @click="onReactivate(item)"
              >
                {{ t('admin_api_tokens.action.reactivate') }}
              </button>
              <button
                v-if="item.status !== 'revoked'"
                type="button"
                class="fh-btn-text danger"
                :disabled="busyTokenId === item.id"
                @click="onRevoke(item)"
              >
                {{ t('admin_api_tokens.action.revoke') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <p v-else class="fh-field-help empty">
        {{ t('admin_api_tokens.empty') }}
      </p>

      <Pager v-model:page="page" :total="total" :page-size="pageSize" />
    </template>
  </div>
</template>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: var(--fh-space-4);
}

.create-form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  max-width: 520px;
  margin-bottom: var(--fh-space-4);
  padding-bottom: var(--fh-space-4);
  border-bottom: 1px solid var(--fh-hairline);
}

.form-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.25rem;
  margin: 0 0 var(--fh-space-2);
}

.form-actions {
  display: flex;
  gap: var(--fh-space-3);
  align-items: baseline;
  margin-top: var(--fh-space-2);
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

.token-scopes-cell {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-1);
  margin-top: var(--fh-space-1);
  max-width: 320px;
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

.user-suggestions {
  list-style: none;
  margin: var(--fh-space-1) 0 0;
  padding: 0;
  border: 1px solid var(--fh-hairline);
  background: var(--fh-paper-raised);
  max-height: 220px;
  overflow-y: auto;
}

.user-suggest {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--fh-space-2);
  width: 100%;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
  font: inherit;
}

.user-suggest:hover {
  background: var(--fh-paper-sunk);
}

.plaintext-box {
  margin-bottom: var(--fh-space-4);
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

.filters {
  display: flex;
  gap: var(--fh-space-3);
  margin-bottom: var(--fh-space-4);
  align-items: baseline;
}

.search {
  flex: 1;
  max-width: 360px;
}

.status-select {
  font: inherit;
  background: transparent;
  border: var(--fh-border-strong);
  border-radius: var(--fh-radius-sm);
  padding: 4px 8px;
  color: var(--fh-ink);
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}

.token-table {
  width: 100%;
  border-collapse: collapse;
}

.token-table th,
.token-table td {
  text-align: left;
  padding: var(--fh-space-2) var(--fh-space-3);
  border-bottom: 1px solid var(--fh-rule);
  vertical-align: top;
}

.token-table th {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--fh-subtle);
  font-weight: 500;
}

.row-name {
  font-weight: 500;
}

.row-hint {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.actions-col {
  width: 1%;
  white-space: nowrap;
}

.actions {
  display: flex;
  gap: var(--fh-space-2);
  white-space: nowrap;
  justify-content: flex-end;
}

.fh-btn-text.danger {
  color: var(--fh-danger);
}

.empty {
  margin: var(--fh-space-3) 0;
}

</style>
