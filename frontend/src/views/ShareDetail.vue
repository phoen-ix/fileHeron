<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import {
  deleteShare,
  expireShareNow,
  getShare,
  updateShareDownloadLimit,
  updateShareExpiry,
} from '@/api/shares'
import ExpiryPicker from '@/components/ExpiryPicker.vue'
import FileRow from '@/components/FileRow.vue'
import PublicLinkPanel from '@/components/PublicLinkPanel.vue'
import { useApiError } from '@/composables/useApiError'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type { ShareResponse } from '@/types/api'
import { formatInSiteTime } from '@/utils/datetime'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()
const { t, locale } = useI18n()
const { describe } = useApiError()

const share = ref<ShareResponse | null>(null)
const loading = ref(true)
const errorMsg = ref<string | null>(null)
const deleting = ref(false)
const expiringNow = ref(false)
const editingExpiry = ref(false)
const newExpiryLocal = ref<string | null>(null)
const savingExpiry = ref(false)

// v1.1.0 download-limit edit modal state.
const editingLimit = ref(false)
const newLimitValue = ref<number | null>(null)
const newLimitClear = ref(false)
const savingLimit = ref(false)

const isOwner = computed(
  () => share.value?.created_by_id === auth.user?.id,
)
const canManage = computed(
  () => isOwner.value || auth.user?.role === 'admin',
)

const totalSize = computed(() =>
  share.value
    ? share.value.files.reduce((acc, f) => acc + f.size_bytes, 0)
    : 0,
)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getShare(route.params.id as string)
    share.value = data
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onRevoke() {
  if (!share.value) return
  if (!confirm(t('share_detail.revoke_confirm'))) return
  deleting.value = true
  try {
    await deleteShare(share.value.id)
    ui.pushToast(t('share_detail.revoked_toast'), 'success')
    await router.push({ name: 'outbox' })
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    deleting.value = false
  }
}

async function onExpireNow() {
  if (!share.value) return
  if (!confirm(t('share_detail.expire_now_confirm'))) return
  expiringNow.value = true
  try {
    const { data } = await expireShareNow(share.value.id)
    share.value = data
    ui.pushToast(t('share_detail.expire_now_toast'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    expiringNow.value = false
  }
}

function startEditExpiry() {
  if (!share.value) return
  newExpiryLocal.value = null
  editingExpiry.value = true
}

function cancelEditExpiry() {
  editingExpiry.value = false
  newExpiryLocal.value = null
}

async function saveExpiry() {
  if (!share.value || !newExpiryLocal.value) return
  savingExpiry.value = true
  try {
    const utcIso = new Date(newExpiryLocal.value).toISOString()
    const { data } = await updateShareExpiry(share.value.id, utcIso)
    share.value = data
    editingExpiry.value = false
    newExpiryLocal.value = null
    ui.pushToast(t('share_detail.expiry_saved_toast'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    savingExpiry.value = false
  }
}

function startEditLimit() {
  if (!share.value) return
  newLimitValue.value = share.value.download_limit
  newLimitClear.value = false
  editingLimit.value = true
}

function cancelEditLimit() {
  editingLimit.value = false
  newLimitValue.value = null
  newLimitClear.value = false
}

async function saveLimit() {
  if (!share.value) return
  if (!newLimitClear.value && (!newLimitValue.value || newLimitValue.value <= 0)) {
    return
  }
  savingLimit.value = true
  try {
    const { data } = await updateShareDownloadLimit(share.value.id, {
      limit: newLimitClear.value ? null : newLimitValue.value,
      clear: newLimitClear.value,
    })
    share.value = data
    editingLimit.value = false
    newLimitValue.value = null
    newLimitClear.value = false
    ui.pushToast(t('share_detail.limit_saved_toast'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    savingLimit.value = false
  }
}

async function onFileDeleted(_fileId: string) {
  if (!share.value) return
  ui.pushToast(t('share_detail.file_deleted_toast'), 'success')
  // Re-fetch the share so the state pill picks up an auto-revoke when
  // the last file is removed (backend transitions active → revoked
  // with audit reason `last_file_deleted`). A naive local filter would
  // hide the file row but leave the stale "active" badge.
  await load()
}

function formatDate(iso: string): string {
  return formatInSiteTime(iso, locale.value)
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

function pillForState(state: string): 'active' | 'warn' | 'danger' | undefined {
  if (state === 'active') return 'active'
  if (state === 'expired') return 'warn'
  if (state === 'revoked' || state === 'deleted') return 'danger'
  return undefined
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="share">
      <span class="fh-eyebrow">
        {{ t(`share_detail.eyebrow.${share.kind}`) }} · {{ t('share_detail.id') }}
        <span class="fh-mono id-token">{{ share.id.slice(0, 8) }}</span>
      </span>
      <h1 class="fh-display-md subject">
        {{ share.effective_subject || t('share_detail.no_subject') }}
      </h1>

      <div class="meta-row">
        <span class="fh-pill" :data-state="pillForState(share.state)">
          {{ t(`share_state.${share.state}`) }}
        </span>
        <span class="fh-kv">
          <span class="fh-kv-label">{{ t('share_detail.expires') }}</span>
          <span class="fh-kv-value">{{ formatDate(share.expires_at) }}</span>
          <button
            v-if="canManage && share.state === 'active' && !editingExpiry"
            type="button"
            class="fh-btn-text edit-link"
            @click="startEditExpiry"
          >
            {{ t('share_detail.edit_expiry') }}
          </button>
        </span>
        <span class="fh-kv">
          <span class="fh-kv-label">{{ t('share_detail.created') }}</span>
          <span class="fh-kv-value">{{ formatDate(share.created_at) }}</span>
        </span>
        <span class="fh-kv">
          <span class="fh-kv-label">{{ t('share_detail.total') }}</span>
          <span class="fh-kv-value">{{ formatBytes(totalSize) }}</span>
        </span>
        <span class="fh-kv">
          <span class="fh-kv-label">{{ t('share_detail.download_limit') }}</span>
          <span class="fh-kv-value">
            <template v-if="share.download_limit !== null">
              {{ t('share_detail.download_limit_value', {
                used: share.download_limit - (share.downloads_remaining ?? 0),
                total: share.download_limit,
              }) }}
            </template>
            <template v-else>{{ t('share_detail.download_limit_none') }}</template>
          </span>
          <button
            v-if="canManage && share.state === 'active' && !editingLimit"
            type="button"
            class="fh-btn-text edit-link"
            @click="startEditLimit"
          >
            {{ t('share_detail.edit_limit') }}
          </button>
        </span>
      </div>

      <div v-if="editingLimit" class="edit-expiry-panel">
        <label class="fh-field">
          <input
            v-model="newLimitClear"
            type="checkbox"
          />
          <span class="toggle-name" style="margin-left: 8px;">{{ t('share_detail.download_limit_clear') }}</span>
        </label>
        <label v-if="!newLimitClear" class="fh-field">
          <span class="fh-field-label">{{ t('share_detail.download_limit_new_label') }}</span>
          <input
            v-model.number="newLimitValue"
            class="fh-field-input fh-field-mono"
            type="number"
            min="1"
            max="100000"
            :placeholder="t('share_detail.download_limit_placeholder')"
          />
        </label>
        <div class="edit-expiry-actions">
          <button
            type="button"
            class="fh-btn"
            :disabled="savingLimit || (!newLimitClear && (!newLimitValue || newLimitValue <= 0))"
            @click="saveLimit"
          >
            {{ savingLimit ? t('common.loading') : t('common.save') }}
          </button>
          <button
            type="button"
            class="fh-btn-text"
            :disabled="savingLimit"
            @click="cancelEditLimit"
          >
            {{ t('common.cancel') }}
          </button>
        </div>
      </div>

      <div v-if="editingExpiry" class="edit-expiry-panel">
        <ExpiryPicker v-model="newExpiryLocal" />
        <div class="edit-expiry-actions">
          <button
            type="button"
            class="fh-btn"
            :disabled="!newExpiryLocal || savingExpiry"
            @click="saveExpiry"
          >
            {{ savingExpiry ? t('common.loading') : t('common.save') }}
          </button>
          <button
            type="button"
            class="fh-btn-text"
            :disabled="savingExpiry"
            @click="cancelEditExpiry"
          >
            {{ t('common.cancel') }}
          </button>
        </div>
      </div>

      <div v-if="share.recipient_groups.length > 0" class="recipient-groups">
        <span class="group-eyebrow">{{ t('share_detail.targeted_groups') }}</span>
        <span
          v-for="g in share.recipient_groups"
          :key="g.id"
          class="group-chip"
          :data-inbox="g.is_company_inbox"
        >
          ◇ {{ g.name }}
          <span v-if="g.is_company_inbox" class="inbox-flag fh-mono">
            {{ t('recipient.inbox_flag') }}
          </span>
        </span>
      </div>

      <div v-if="share.message" class="message">{{ share.message }}</div>

      <hr class="fh-rule" />

      <h2 class="files-h2">{{ t('share_detail.files_heading', { n: share.files.length }) }}</h2>

      <ul v-if="share.files.length > 0" class="files">
        <FileRow
          v-for="f in share.files"
          :key="f.id"
          :file="f"
          :can-delete="isOwner && f.state !== 'infected'"
          @deleted="onFileDeleted"
        />
      </ul>
      <p v-else class="fh-field-help">{{ t('share_detail.empty') }}</p>

      <PublicLinkPanel
        v-if="isOwner && share.state === 'active'"
        :share-id="share.id"
      />

      <div v-if="canManage && share.state === 'active'" class="owner-actions">
        <button
          type="button"
          class="fh-btn-ghost fh-btn"
          :disabled="expiringNow"
          @click="onExpireNow"
        >
          {{ expiringNow ? t('common.loading') : t('share_detail.expire_now_cta') }}
        </button>
        <button
          type="button"
          class="fh-btn-danger fh-btn"
          :disabled="deleting"
          @click="onRevoke"
        >
          {{ deleting ? t('common.loading') : t('share_detail.revoke_cta') }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.loading {
  color: var(--fh-subtle);
  font-size: var(--fh-text-body-md);
  padding: var(--fh-space-5) 0;
}

.id-token {
  background: var(--fh-paper-raised);
  padding: 1px 6px;
  border-radius: var(--fh-radius-sm);
  letter-spacing: 0.06em;
}

.subject {
  margin-top: var(--fh-space-2);
}

.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-4);
  align-items: center;
  margin: var(--fh-space-3) 0;
}

.message {
  background: var(--fh-paper-raised);
  border-left: 2px solid var(--fh-hairline-strong);
  padding: var(--fh-space-3) var(--fh-space-4);
  margin: var(--fh-space-3) 0;
  white-space: pre-wrap;
  line-height: 1.6;
  color: var(--fh-ink-soft);
}

.recipient-groups {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-2);
  align-items: center;
  margin: var(--fh-space-2) 0;
}

.group-eyebrow {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--fh-subtle);
}

.group-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-1);
  padding: 2px var(--fh-space-2);
  background: var(--fh-paper-raised);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  font-size: var(--fh-text-body-sm);
  color: var(--fh-ink);
}

.group-chip[data-inbox='true'] {
  background: var(--fh-accent-soft);
  border-color: rgba(180, 83, 9, 0.3);
}

.inbox-flag {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-accent);
}

.files-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  letter-spacing: -0.01em;
  margin: var(--fh-space-3) 0;
}

.files {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: var(--fh-border);
}

.owner-actions {
  margin-top: var(--fh-space-5);
  display: flex;
  justify-content: flex-end;
  gap: var(--fh-space-3);
}

.edit-link {
  margin-left: var(--fh-space-2);
  font-size: var(--fh-text-mono-sm);
}

.edit-expiry-panel {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  margin-top: var(--fh-space-3);
  padding: var(--fh-space-3);
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
  max-width: 480px;
}

.edit-expiry-actions {
  display: flex;
  gap: var(--fh-space-3);
  align-items: baseline;
}
</style>
