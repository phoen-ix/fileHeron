<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { asEnvelope } from '@/api/client'
import { getDownloadUrl, getPreviewUrl, getShareZipUrl } from '@/api/files'
import {
  approveShare,
  deleteShare,
  expireShareNow,
  getShare,
  registerFilesAdded,
  rejectShare,
  resubmitShare,
  updateShareDownloadLimit,
  updateShareExpiry,
} from '@/api/shares'
import ExpiryPicker from '@/components/ExpiryPicker.vue'
import FilePreviewModal from '@/components/FilePreviewModal.vue'
import FileRow from '@/components/FileRow.vue'
import FileUploadArea from '@/components/FileUploadArea.vue'
import PublicLinkPanel from '@/components/PublicLinkPanel.vue'
import { useApiError } from '@/composables/useApiError'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUpload } from '@/composables/useUpload'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type { FileInShareResponse, ShareResponse } from '@/types/api'
import { formatBytes } from '@/utils/bytes'
import { formatExpiryInSiteTime, siteLocalIsoToUtcIso } from '@/utils/datetime'
import { shareStatePill } from '@/utils/statePill'

const route = useRoute()
const auth = useAuthStore()
const ui = useUiStore()
const { t, locale } = useI18n()
const { formatDate } = useSiteDateFormat()
const { describe } = useApiError()

const share = ref<ShareResponse | null>(null)
const loading = ref(true)
const errorMsg = ref<string | null>(null)
const expiringNow = ref(false)
const editingExpiry = ref(false)
// Picker emits string (local ISO), null (= "Never" preset), or
// undefined (mount-before-emit). saveExpiry maps these to the API:
// string → set; null → clear; undefined → no-op.
const newExpiryLocal = ref<string | null | undefined>(undefined)
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

// Share-approval workflow (v1.24.0).
const approving = ref(false)
const rejecting = ref(false)
const resubmitting = ref(false)
const showRejectForm = ref(false)
const rejectReason = ref('')

async function onApprove() {
  if (!share.value) return
  approving.value = true
  try {
    const { data } = await approveShare(
      share.value.id,
      share.value.content_fingerprint,
    )
    share.value = data
    ui.pushToast(t('approvals.approved_toast'), 'success')
  } catch (err) {
    // The owner added or removed something while this page was open. Reload so
    // the approver decides on what is actually there now.
    if (asEnvelope(err)?.code === 'CONTENT_CHANGED') {
      await load()
      ui.pushToast(t('approvals.content_changed'), 'warn')
      return
    }
    ui.pushToast(describe(err), 'error')
  } finally {
    approving.value = false
  }
}

async function onReject() {
  if (!share.value) return
  rejecting.value = true
  try {
    const { data } = await rejectShare(
      share.value.id,
      rejectReason.value.trim() || null,
    )
    share.value = data
    showRejectForm.value = false
    rejectReason.value = ''
    ui.pushToast(t('approvals.rejected_toast'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    rejecting.value = false
  }
}

async function onResubmit() {
  if (!share.value) return
  resubmitting.value = true
  try {
    const { data } = await resubmitShare(share.value.id)
    share.value = data
    ui.pushToast(t('approvals.resubmitted_toast'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    resubmitting.value = false
  }
}

async function onDiscard() {
  if (!share.value) return
  if (!(await ui.confirm({ message: t('approvals.discard_confirm'), danger: true }))) return
  try {
    await deleteShare(share.value.id)
    const { data } = await getShare(share.value.id)
    share.value = data
    ui.pushToast(t('approvals.discarded_toast'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

// Bulk-ZIP download of every clean file. Hidden when nothing is downloadable
// or the share's download budget is spent.
const downloadingZip = ref(false)
const canDownloadZip = computed(
  () =>
    !!share.value &&
    share.value.files.some((f) => f.state === 'clean') &&
    (share.value.download_limit === null || (share.value.downloads_remaining ?? 0) > 0),
)
async function onDownloadZip() {
  if (!share.value) return
  downloadingZip.value = true
  try {
    const { data } = await getShareZipUrl(share.value.id)
    window.location.href = data.url
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    downloadingZip.value = false
  }
}

// Add-files panel (owner + active only; uploads are owner-only server-side).
const addUpload = useUpload(computed(() => share.value?.id ?? null))
const showAddFiles = ref(false)
const addingBusy = ref(false)
const notifyOnAdd = ref(auth.user?.share_notify_recipients_default ?? true)

// 'finalizing' counts as done (tusd post-finish races client-side; the
// register response re-reads authoritative file state). Mirrors ShareCreate.
const addAllDone = computed(
  () =>
    addUpload.items.value.length > 0 &&
    addUpload.items.value.every(
      (i) => i.state === 'done' || i.state === 'finalizing',
    ),
)

function startAddFiles() {
  addUpload.reset()
  notifyOnAdd.value = auth.user?.share_notify_recipients_default ?? true
  showAddFiles.value = true
}

function cancelAddFiles() {
  addUpload.reset()
  showAddFiles.value = false
}

async function onUploadAdded() {
  if (!share.value || addUpload.isActive.value) return
  addingBusy.value = true
  try {
    if (addUpload.items.value.some((i) => i.state === 'queued')) {
      await addUpload.start()
    }
    if (!addAllDone.value) {
      ui.pushToast(t('share_detail.add_files_has_errors'), 'error')
      return
    }
    const fileIds = addUpload.items.value
      .filter((i) => i.fileId && (i.state === 'done' || i.state === 'finalizing'))
      .map((i) => i.fileId as string)
    if (fileIds.length === 0) return
    const { data } = await registerFilesAdded(share.value.id, {
      notify: notifyOnAdd.value,
      file_ids: fileIds,
    })
    share.value = data
    addUpload.reset()
    showAddFiles.value = false
    ui.pushToast(t('share_detail.files_added_toast', { n: fileIds.length }), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    addingBusy.value = false
  }
}

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

async function onEndShare() {
  if (!share.value) return
  if (!(await ui.confirm({ message: t('share_detail.end_share_confirm'), danger: true }))) return
  expiringNow.value = true
  try {
    const { data } = await expireShareNow(share.value.id)
    share.value = data
    ui.pushToast(t('share_detail.end_share_toast'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    expiringNow.value = false
  }
}

function startEditExpiry() {
  if (!share.value) return
  newExpiryLocal.value = undefined
  editingExpiry.value = true
}

function cancelEditExpiry() {
  editingExpiry.value = false
  newExpiryLocal.value = undefined
}

async function saveExpiry() {
  if (!share.value || newExpiryLocal.value === undefined) return
  savingExpiry.value = true
  try {
    const { data } =
      newExpiryLocal.value === null
        ? await updateShareExpiry(share.value.id, { clear: true })
        : await updateShareExpiry(share.value.id, {
            // newExpiryLocal is a site-tz wall-clock string from ExpiryPicker;
            // convert via the site tz, not the browser's (matches ShareCreate).
            expires_at: siteLocalIsoToUtcIso(newExpiryLocal.value),
          })
    share.value = data
    editingExpiry.value = false
    newExpiryLocal.value = undefined
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

function formatExpiry(iso: string | null): string {
  return formatExpiryInSiteTime(iso, locale.value, t('expiry.never_label'))
}

// In-browser preview. Mint an inline `?dt=` URL on open; preview never
// consumes the share's download budget (separate endpoint).
const previewOpen = ref(false)
const previewFile = ref<FileInShareResponse | null>(null)
const previewUrl = ref<string | null>(null)

async function openPreview(file: FileInShareResponse) {
  previewFile.value = file
  previewUrl.value = null
  previewOpen.value = true
  try {
    const { data } = await getPreviewUrl(file.id)
    previewUrl.value = data.url
  } catch (err) {
    previewOpen.value = false
    ui.pushToast(describe(err), 'error')
  }
}

function closePreview() {
  previewOpen.value = false
  previewFile.value = null
  previewUrl.value = null
}

async function onPreviewDownload() {
  if (!previewFile.value) return
  try {
    const { data } = await getDownloadUrl(previewFile.value.id)
    window.location.href = data.url
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <div
v-else-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="share">
      <span class="fh-eyebrow">
        {{ t(`share_detail.eyebrow.${share.kind}`) }} · {{ t('share_detail.id') }}
        <span class="fh-mono id-token">{{ share.id.slice(0, 8) }}</span>
      </span>
      <h1 class="fh-display-md subject">
        {{ share.effective_subject || t('share_detail.no_subject') }}
      </h1>

      <div class="meta-row">
        <span class="fh-pill" :data-state="shareStatePill(share.state)">
          {{ t(`share_state.${share.state}`) }}
        </span>
        <span class="fh-kv">
          <span class="fh-kv-label">{{ t('share_detail.expires') }}</span>
          <span class="fh-kv-value">{{ formatExpiry(share.expires_at) }}</span>
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
            :disabled="newExpiryLocal === undefined || savingExpiry"
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

      <!-- Share-approval (v1.24.0): owner banners + approver actions. -->
      <div
        v-if="isOwner && share.state === 'pending_approval'"
        class="approval-box"
      >
        <p class="fh-notice" data-tone="info">
          {{ t('approvals.pending_owner_banner') }}
        </p>
        <button type="button" class="fh-btn-text" @click="onDiscard">
          {{ t('approvals.discard_cta') }}
        </button>
      </div>

      <div
        v-if="isOwner && share.state === 'rejected'"
        class="approval-box"
      >
        <p
class="fh-notice" role="alert"
        data-tone="error">
          {{ t('approvals.rejected_owner_banner') }}
        </p>
        <p v-if="share.rejection_reason" class="reject-reason">
          <span class="fh-kv-label">{{ t('approvals.reason_label') }}</span>
          {{ share.rejection_reason }}
        </p>
        <div class="approver-buttons">
          <button
            type="button"
            class="fh-btn"
            :disabled="resubmitting"
            @click="onResubmit"
          >
            {{ resubmitting ? t('common.loading') : t('approvals.resubmit_cta') }}
          </button>
          <button type="button" class="fh-btn-text" @click="onDiscard">
            {{ t('approvals.discard_cta') }}
          </button>
        </div>
      </div>

      <div
        v-if="share.viewer_can_approve && share.state === 'pending_approval'"
        class="approval-box approver-actions"
      >
        <!-- A public link attached to a pending share is inert now and live the
             instant this button is pressed. Approving is what publishes it, so
             it has to be on screen at the moment of the decision. -->
        <p
          v-if="share.public_link_summary"
          class="fh-notice"
          data-tone="warn"
        >
          {{
            share.public_link_summary.has_password
              ? t('approvals.public_link_warning_password')
              : t('approvals.public_link_warning')
          }}
        </p>
        <p class="fh-field-help">{{ t('approvals.decide_help') }}</p>
        <div v-if="!showRejectForm" class="approver-buttons">
          <button
            type="button"
            class="fh-btn"
            :disabled="approving"
            @click="onApprove"
          >
            {{ approving ? t('common.loading') : t('approvals.approve_cta') }}
          </button>
          <button
            type="button"
            class="fh-btn-ghost fh-btn"
            @click="showRejectForm = true"
          >
            {{ t('approvals.reject_cta') }}
          </button>
        </div>
        <form v-else class="reject-form" @submit.prevent="onReject">
          <label class="fh-field">
            <span class="fh-field-label">{{ t('approvals.reason_label') }}</span>
            <textarea
              v-model="rejectReason"
              class="fh-field-input"
              rows="3"
              maxlength="1000"
              :placeholder="t('approvals.reason_placeholder')"
            />
          </label>
          <div class="approver-buttons">
            <button
              type="submit"
              class="fh-btn-danger fh-btn"
              :disabled="rejecting"
            >
              {{ rejecting ? t('common.loading') : t('approvals.confirm_reject_cta') }}
            </button>
            <button
              type="button"
              class="fh-btn-ghost fh-btn"
              :disabled="rejecting"
              @click="showRejectForm = false"
            >
              {{ t('common.cancel') }}
            </button>
          </div>
        </form>
      </div>

      <hr class="fh-rule" />

      <div class="files-head">
        <h2 class="files-h2">{{ t('share_detail.files_heading', { n: share.files.length }) }}</h2>
        <button
          v-if="canDownloadZip"
          type="button"
          class="fh-btn-text edit-link"
          :disabled="downloadingZip"
          @click="onDownloadZip"
        >
          {{ downloadingZip ? t('common.loading') : t('share_detail.download_all_zip') }}
        </button>
        <button
          v-if="isOwner && share.state === 'active' && !showAddFiles"
          type="button"
          class="fh-btn-text edit-link"
          data-testid="add-files-toggle"
          @click="startAddFiles"
        >
          {{ t('share_detail.add_files') }}
        </button>
      </div>

      <div v-if="showAddFiles" class="edit-expiry-panel add-files-panel">
        <FileUploadArea
          :items="addUpload.items.value"
          :disabled="addingBusy"
          @add="addUpload.add"
          @remove="addUpload.remove"
          @retry="addUpload.retry"
        />
        <label class="notify-row">
          <input v-model="notifyOnAdd" type="checkbox" />
          <span class="toggle-name">{{ t('share_detail.add_files_notify') }}</span>
        </label>
        <div class="edit-expiry-actions">
          <button
            type="button"
            class="fh-btn"
            data-testid="add-files-submit"
            :disabled="addingBusy || addUpload.isActive.value || addUpload.items.value.length === 0"
            @click="onUploadAdded"
          >
            {{ addingBusy || addUpload.isActive.value ? t('common.loading') : t('share_detail.add_files_cta') }}
          </button>
          <button
            type="button"
            class="fh-btn-text"
            :disabled="addingBusy || addUpload.isActive.value"
            @click="cancelAddFiles"
          >
            {{ t('common.cancel') }}
          </button>
        </div>
      </div>

      <ul v-if="share.files.length > 0" class="files">
        <FileRow
          v-for="f in share.files"
          :key="f.id"
          :file="f"
          :can-delete="isOwner && f.state !== 'infected'"
          @deleted="onFileDeleted"
          @preview="openPreview"
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
          class="fh-btn-danger fh-btn"
          :disabled="expiringNow"
          @click="onEndShare"
        >
          {{ expiringNow ? t('common.loading') : t('share_detail.end_share_cta') }}
        </button>
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

.files-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fh-space-3);
}

.files-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  letter-spacing: -0.01em;
  margin: var(--fh-space-3) 0;
}

.add-files-panel {
  max-width: none;
}

.notify-row {
  display: flex;
  align-items: center;
  gap: var(--fh-space-2);
  font-size: var(--fh-text-body-sm);
  color: var(--fh-ink-soft);
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

.approval-box {
  margin: var(--fh-space-3) 0;
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  align-items: flex-start;
}

.approver-buttons {
  display: flex;
  gap: var(--fh-space-3);
  align-items: center;
}

.reject-reason {
  background: var(--fh-paper-raised);
  border-left: 2px solid var(--fh-hairline-strong);
  padding: var(--fh-space-2) var(--fh-space-3);
  margin: 0;
}

.reject-form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  width: 100%;
  max-width: 480px;
}
</style>
