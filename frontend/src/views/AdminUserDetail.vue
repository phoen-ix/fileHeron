<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import {
  adminDeleteFile,
  adminListFiles,
  adminListSessions,
  adminRevokeSession,
  adminRevokeUserSessions,
  changeUserEmail,
  erasePreflight,
  eraseUser,
  erasureReceiptPdf,
  forcePasswordReset,
  getUser,
  listMailLog,
  updateUser,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { downloadBlob } from '@/utils/downloadBlob'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import type {
  AdminFileItem,
  AdminMailRow,
  AdminSessionRow,
  AdminUserItem,
  ErasePreflight,
  UserRole,
} from '@/types/api'
import { formatBytes } from '@/utils/bytes'
import { uaShort } from '@/utils/ua'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const { formatDate } = useSiteDateFormat()
const { describe, describeBlob } = useApiError()
const ui = useUiStore()

const user = ref<AdminUserItem | null>(null)
const loading = ref(true)

const editName = ref('')
const editRole = ref<UserRole>('client')
const editQuota = ref<number | null>(null)
const loadError = ref<string | null>(null)
const editDisabled = ref(false)
const saving = ref(false)
const savingError = ref<string | null>(null)

const resetting = ref(false)
const resetTokenPlaintext = ref<string | null>(null)

const newEmail = ref('')
const skipVerification = ref(false)
const changingEmail = ref(false)
const changeEmailError = ref<string | null>(null)
const changeEmailLink = ref<string | null>(null)
// `verify_both` mints TWO tokens; only the new-address one was ever shown, so
// on an SMTP-less instance the change could never complete (audit #2).
const changeEmailOldLink = ref<string | null>(null)

const eraseStep = ref<0 | 1 | 2>(0)
const erasing = ref(false)

const sessions = ref<AdminSessionRow[]>([])
const sessionsLoading = ref(false)
const revokingSessionId = ref<number | null>(null)
const revokingAll = ref(false)

const files = ref<AdminFileItem[]>([])
const filesLoading = ref(false)
const deletingFileId = ref<string | null>(null)

const emails = ref<AdminMailRow[]>([])
const emailsLoading = ref(false)

// Mirrors the backend's erased-sentinel test (services/erasure.py): erasure
// rewrites email to `erased-<id>@erased.invalid` (display_name becomes
// `[erased]`), so detect on the email suffix, not the display name.
const isErased = computed(() => user.value?.email?.endsWith('@erased.invalid') ?? false)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const { data } = await getUser(Number(route.params.id))
    user.value = data
    editName.value = data.display_name
    editRole.value = data.role
    editQuota.value = data.quota_bytes
    editDisabled.value = data.is_disabled
    await loadSessions()
    await loadFiles()
    await loadEmails()
  } catch (err) {
    // Without this the page painted the admin sidebar and an entirely empty
    // content area - no message, no not-found state, nothing to suggest a
    // request had failed. A bookmarked link to a purged account read as a
    // broken admin panel (audit #2).
    loadError.value = describe(err)
    user.value = null
  } finally {
    loading.value = false
  }
}

async function loadEmails() {
  if (!user.value) return
  emailsLoading.value = true
  try {
    const { data } = await listMailLog({
      recipient_user_id: user.value.id,
      page_size: 50,
    })
    emails.value = data.items
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    emailsLoading.value = false
  }
}

async function loadFiles() {
  if (!user.value) return
  filesLoading.value = true
  try {
    const { data } = await adminListFiles({
      uploader_id: user.value.id,
      include_inactive: false,
      sort: 'uploaded_at',
      direction: 'desc',
      page_size: 100,
    })
    files.value = data.items
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    filesLoading.value = false
  }
}

async function onDeleteFile(f: AdminFileItem) {
  if (deletingFileId.value) return
  if (!(await ui.confirm({ message: t('admin_user_detail.files_delete_confirm', { name: f.filename }), danger: true }))) return
  deletingFileId.value = f.file_id
  try {
    await adminDeleteFile(f.file_id)
    ui.pushToast(t('admin_file_history.deleted_toast', { name: f.filename }), 'success')
    await loadFiles()
    await load()  // refresh the Storage figure
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    deletingFileId.value = null
  }
}

async function loadSessions() {
  if (!user.value) return
  sessionsLoading.value = true
  try {
    const { data } = await adminListSessions({
      user_id: user.value.id,
      sort: 'last_used_at',
      direction: 'asc',
      page_size: 100,
    })
    sessions.value = data.items
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    sessionsLoading.value = false
  }
}

async function onRevokeSession(s: AdminSessionRow) {
  if (revokingSessionId.value) return
  revokingSessionId.value = s.id
  try {
    await adminRevokeSession(s.id)
    ui.pushToast(t('admin_sessions.revoked_toast'), 'success')
    await loadSessions()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    revokingSessionId.value = null
  }
}

async function onRevokeAllSessions() {
  if (!user.value || revokingAll.value) return
  if (!(await ui.confirm({ message: t('admin_user_detail.sessions_revoke_all_confirm'), danger: true }))) return
  revokingAll.value = true
  try {
    const { data } = await adminRevokeUserSessions(user.value.id)
    ui.pushToast(t('admin_sessions.revoked_all_toast', { n: data.revoked }), 'success')
    await loadSessions()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    revokingAll.value = false
  }
}

async function onSave() {
  if (!user.value) return
  saving.value = true
  savingError.value = null
  try {
    const payload: Parameters<typeof updateUser>[1] = {}
    if (editName.value !== user.value.display_name) payload.display_name = editName.value
    if (editRole.value !== user.value.role) payload.role = editRole.value
    // `v-model.number` on an empty field yields '' (not null), which the API
    // rejects 422 and the SPA reports as "Something didn't work" - so the
    // "leave blank for unlimited" the help text promises was impossible, and 0
    // (the only input that worked) reads as "zero bytes allowed" (audit #2).
    const quota =
      editQuota.value === null || (editQuota.value as unknown as string) === ''
        ? null
        : Number(editQuota.value)
    if (quota !== user.value.quota_bytes) payload.quota_bytes = quota
    if (editDisabled.value !== user.value.is_disabled) payload.is_disabled = editDisabled.value

    if (Object.keys(payload).length === 0) {
      ui.pushToast(t('admin_user_detail.no_changes'), 'info')
      return
    }
    await updateUser(user.value.id, payload)
    ui.pushToast(t('common.saved'), 'success')
    await load()
  } catch (err) {
    savingError.value = describe(err)
  } finally {
    saving.value = false
  }
}

async function onForceReset() {
  if (!user.value) return
  if (!(await ui.confirm({ message: t('admin_user_detail.force_reset_confirm') }))) return
  resetting.value = true
  try {
    const { data } = await forcePasswordReset(user.value.id)
    resetTokenPlaintext.value = data.plaintext_token
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    resetting.value = false
  }
}

async function onChangeEmail() {
  if (!user.value || changingEmail.value) return
  const next = newEmail.value.trim()
  if (!next || next === user.value.email) return
  if (!(await ui.confirm({ message: t('admin_user_detail.change_email_confirm') }))) return
  changingEmail.value = true
  changeEmailError.value = null
  changeEmailLink.value = null
  changeEmailOldLink.value = null
  try {
    const { data } = await changeUserEmail(user.value.id, {
      new_email: next,
      skip_verification: skipVerification.value,
    })
    if (data.applied) {
      ui.pushToast(t('admin_user_detail.change_email_applied'), 'success')
    } else {
      ui.pushToast(t('admin_user_detail.change_email_pending'), 'success')
      changeEmailLink.value = data.confirm_url
      changeEmailOldLink.value = data.old_confirm_url ?? null
    }
    if (data.oidc_reset) {
      ui.pushToast(t('admin_user_detail.change_email_oidc_reset'), 'info')
    }
    newEmail.value = ''
    skipVerification.value = false
    await load()
  } catch (err) {
    changeEmailError.value = describe(err)
  } finally {
    changingEmail.value = false
  }
}

// What the erase is about to destroy. The endpoint shipped with the feature
// and nothing called it, so both confirmation steps asked for an irreversible
// decision without showing its cost (audit 2026-07-30, flow-erasure-10).
const preflight = ref<ErasePreflight | null>(null)
const receiptAuditId = ref<number | null>(null)

async function onErase() {
  if (!user.value) return
  if (eraseStep.value === 0) {
    eraseStep.value = 1
    try {
      const { data } = await erasePreflight(user.value.id)
      preflight.value = data
    } catch {
      // Best-effort: a failed pre-flight must not block the erasure itself,
      // it just means the dialog shows no counts.
      preflight.value = null
    }
    return
  }
  if (eraseStep.value === 1) {
    eraseStep.value = 2
    return
  }
  erasing.value = true
  try {
    const { data } = await eraseUser(user.value.id)
    receiptAuditId.value = data.audit_id
    ui.pushToast(
      t('admin_user_detail.erased_toast', {
        n: data.deleted_files,
        bytes: data.deleted_bytes,
      }),
      'success',
    )
    // Offer the receipt BEFORE navigating away - it is the document the admin
    // hands to the data subject, and the erase response is the only place its
    // audit id appears.
    if (data.audit_id !== null) {
      await onDownloadReceipt(data.audit_id)
    }
    await router.push({ name: 'admin-users' })
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    erasing.value = false
  }
}


async function onDownloadReceipt(auditId: number) {
  try {
    const { data } = await erasureReceiptPdf(auditId)
    downloadBlob(data as Blob, `erasure-receipt-${auditId}.pdf`)
  } catch (err) {
    ui.pushToast(await describeBlob(err), 'error')
  }
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <div
v-else-if="loadError" class="fh-notice" role="alert"
        data-tone="error">
      {{ loadError }}
    </div>

    <template v-else-if="user">
      <span class="fh-eyebrow">{{ t('admin_user_detail.eyebrow') }}</span>
      <h1 class="fh-display-md">{{ user.display_name }}</h1>
      <p class="meta-line">
        <span class="fh-mono">{{ user.email }}</span>
        <span class="fh-mono role">{{ user.role }}</span>
        <span v-if="user.has_2fa" class="fh-pill" data-state="active">2FA on</span>
        <span v-else class="fh-pill">2FA off</span>
        <span v-if="user.is_disabled" class="fh-pill" data-state="danger">disabled</span>
        <span v-if="!user.email_verified" class="fh-pill" data-state="warn">
          {{ t('admin_user_detail.email_unverified_pill') }}
        </span>
      </p>

      <hr class="fh-rule" />

      <h2 class="section-h2">{{ t('admin_user_detail.profile') }}</h2>
      <form class="edit-form" @submit.prevent="onSave">
        <label class="fh-field">
          <span class="fh-field-label">{{ t('common.display_name') }}</span>
          <input v-model.trim="editName" class="fh-field-input" :disabled="isErased" />
        </label>
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_user_detail.role') }}</span>
          <select v-model="editRole" class="fh-field-input" :disabled="isErased">
            <option value="admin">admin</option>
            <option value="employee">employee</option>
            <option value="client">client</option>
          </select>
        </label>
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_user_detail.quota') }}</span>
          <input
            v-model.number="editQuota"
            class="fh-field-input fh-field-mono"
            type="number"
            min="0"
            :placeholder="t('admin_user_detail.quota_unlimited')"
            :disabled="isErased"
          />
          <span class="fh-field-help">{{ t('admin_user_detail.quota_help') }}</span>
        </label>
        <label class="checkbox">
          <input v-model="editDisabled" type="checkbox" :disabled="isErased" />
          <span class="cb-label">{{ t('admin_user_detail.disable_label') }}</span>
        </label>
        <div
v-if="savingError" class="fh-notice" role="alert"
        data-tone="error">{{ savingError }}</div>
        <div class="actions">
          <button type="submit" class="fh-btn" :disabled="saving || isErased">
            {{ saving ? t('common.loading') : t('common.save') }}
          </button>
        </div>
      </form>

      <hr class="fh-rule" />

      <h2 class="section-h2">{{ t('admin_user_detail.security') }}</h2>
      <div class="security-row">
        <div>
          <p class="fh-field-help">{{ t('admin_user_detail.created') }}: {{ formatDate(user.created_at) }}</p>
          <p class="fh-field-help">{{ t('admin_user_detail.last_login') }}: {{ formatDate(user.last_login_at) }}</p>
        </div>
        <div class="actions">
          <button
            type="button"
            class="fh-btn-ghost fh-btn"
            :disabled="resetting || isErased"
            @click="onForceReset"
          >
            {{ t('admin_user_detail.force_reset') }}
          </button>
        </div>
      </div>

      <div v-if="resetTokenPlaintext" class="reset-box fh-rise">
        <div class="reset-eyebrow">{{ t('admin_user_detail.reset_eyebrow') }}</div>
        <p class="warning">{{ t('admin_user_detail.reset_warning') }}</p>
        <pre class="token fh-mono">{{ resetTokenPlaintext }}</pre>
      </div>

      <form class="change-email-form" @submit.prevent="onChangeEmail">
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_user_detail.change_email') }}</span>
          <input
            v-model.trim="newEmail"
            class="fh-field-input"
            type="email"
            autocomplete="off"
            :placeholder="t('admin_user_detail.change_email_placeholder')"
            :disabled="isErased || changingEmail"
          />
          <span class="fh-field-help">{{ t('admin_user_detail.change_email_help') }}</span>
        </label>
        <label class="checkbox">
          <input v-model="skipVerification" type="checkbox" :disabled="isErased || changingEmail" />
          <span class="cb-label">{{ t('admin_user_detail.change_email_skip') }}</span>
        </label>
        <div
v-if="changeEmailError" class="fh-notice" role="alert"
        data-tone="error">{{ changeEmailError }}</div>
        <div class="actions">
          <button
            type="submit"
            class="fh-btn-ghost fh-btn"
            :disabled="changingEmail || isErased || !newEmail"
          >
            {{ changingEmail ? t('common.loading') : t('admin_user_detail.change_email_submit') }}
          </button>
        </div>
      </form>

      <div v-if="changeEmailLink" class="reset-box fh-rise">
        <div class="reset-eyebrow">{{ t('admin_user_detail.change_email_link_eyebrow') }}</div>
        <p class="warning">{{ t('admin_user_detail.change_email_link_help') }}</p>
        <pre class="token fh-mono">{{ changeEmailLink }}</pre>
        <template v-if="changeEmailOldLink">
          <div class="reset-eyebrow">
            {{ t('admin_user_detail.change_email_old_link_eyebrow') }}
          </div>
          <p class="warning">{{ t('admin_user_detail.change_email_old_link_help') }}</p>
          <pre class="token fh-mono">{{ changeEmailOldLink }}</pre>
        </template>
      </div>

      <hr class="fh-rule" />

      <div class="sessions-head">
        <h2 class="section-h2">{{ t('admin_user_detail.sessions') }}</h2>
        <button
          v-if="sessions.length > 0"
          type="button"
          class="fh-btn-text revoke-btn"
          :disabled="revokingAll"
          @click="onRevokeAllSessions"
        >
          {{ revokingAll ? t('common.loading') : t('admin_user_detail.sessions_revoke_all') }}
        </button>
      </div>
      <p class="fh-field-help">{{ t('admin_user_detail.sessions_help') }}</p>

      <div v-if="sessionsLoading" class="loading">{{ t('common.loading') }}</div>
      <ul v-else-if="sessions.length > 0" class="session-list">
        <li v-for="s in sessions" :key="s.id" class="session-item">
          <div class="session-info">
            <span class="session-ua">{{ uaShort(s.created_ua, t('admin_sessions.unknown_device')) }}</span>
            <span class="fh-mono session-meta">
              <span v-if="s.created_ip">{{ s.created_ip }}</span>
              <span>{{ t('admin_sessions.col.last_active') }}: {{ formatDate(s.last_used_at) }}</span>
            </span>
          </div>
          <button
            type="button"
            class="fh-btn-text revoke-btn"
            :disabled="revokingSessionId === s.id"
            @click="onRevokeSession(s)"
          >
            {{ revokingSessionId === s.id ? t('common.loading') : t('admin_sessions.revoke') }}
          </button>
        </li>
      </ul>
      <p v-else class="fh-field-help empty">{{ t('admin_user_detail.sessions_empty') }}</p>

      <hr class="fh-rule" />

      <div class="files-head">
        <h2 class="section-h2">{{ t('admin_user_detail.files') }}</h2>
        <RouterLink
          class="fh-btn-text"
          :to="{ name: 'admin-file-history', query: { uploader_id: user.id } }"
        >
          {{ t('admin_user_detail.files_view_all') }}
        </RouterLink>
      </div>
      <p class="fh-field-help">{{ t('admin_user_detail.files_help') }}</p>

      <div v-if="filesLoading" class="loading">{{ t('common.loading') }}</div>
      <ul v-else-if="files.length > 0" class="file-list">
        <li v-for="f in files" :key="f.file_id" class="file-item">
          <div class="file-info">
            <span class="file-name">{{ f.filename }}</span>
            <span class="fh-mono file-meta">
              <span>{{ formatBytes(f.size_bytes) }}</span>
              <span class="fh-pill" :data-state="f.state === 'clean' ? 'active' : 'warn'">{{ f.state }}</span>
              <span v-if="f.share_subject">{{ f.share_subject }}</span>
            </span>
          </div>
          <button
            type="button"
            class="fh-btn-text revoke-btn"
            :disabled="deletingFileId === f.file_id"
            @click="onDeleteFile(f)"
          >
            {{ deletingFileId === f.file_id ? t('common.loading') : t('admin_file_history.delete') }}
          </button>
        </li>
      </ul>
      <p v-else class="fh-field-help empty">{{ t('admin_user_detail.files_empty') }}</p>

      <hr class="fh-rule" />

      <div class="files-head">
        <h2 class="section-h2">{{ t('admin_user_detail.emails') }}</h2>
        <RouterLink
          class="fh-btn-text"
          :to="{ name: 'admin-mail-log', query: { recipient_user_id: user.id } }"
        >
          {{ t('admin_user_detail.emails_view_all') }}
        </RouterLink>
      </div>
      <p class="fh-field-help">{{ t('admin_user_detail.emails_help') }}</p>

      <div v-if="emailsLoading" class="loading">{{ t('common.loading') }}</div>
      <ul v-else-if="emails.length > 0" class="file-list">
        <li v-for="m in emails" :key="m.id" class="file-item">
          <RouterLink
            class="file-info email-link"
            :to="{ name: 'admin-mail-detail', params: { id: m.id } }"
          >
            <span class="file-name">{{ m.subject }}</span>
            <span class="fh-mono file-meta">
              <span>{{ formatDate(m.created_at, { second: '2-digit' }) }}</span>
              <span
                class="fh-pill"
                :data-state="m.status === 'sent' ? 'active' : (m.status === 'queued' ? 'warn' : 'danger')"
              >{{ m.status }}</span>
              <span>{{ m.category }}</span>
            </span>
          </RouterLink>
        </li>
      </ul>
      <p v-else class="fh-field-help empty">{{ t('admin_user_detail.emails_empty') }}</p>

      <hr class="fh-rule" />

      <div class="danger-zone">
        <h2 class="section-h2">{{ t('admin_user_detail.danger') }}</h2>
        <p class="fh-field-help">{{ t('admin_user_detail.erase_help') }}</p>
        <p v-if="eraseStep === 1" class="fh-notice" data-tone="error" role="alert">
          {{ t('admin_user_detail.erase_step1') }}
        </p>
        <!-- What the erase will destroy, from the pre-flight endpoint. -->
        <ul v-if="eraseStep >= 1 && preflight" class="fh-notice erase-preflight" data-tone="error">
          <li>
            {{
              t('admin_user_detail.erase_preflight_files', {
                n: preflight.files_to_delete,
                bytes: formatBytes(preflight.bytes_to_delete),
              })
            }}
          </li>
          <li>
            {{
              t('admin_user_detail.erase_preflight_shares', {
                created: preflight.shares_created,
                received: preflight.shares_received_to_anonymize,
              })
            }}
          </li>
        </ul>
        <p v-if="eraseStep === 2" class="fh-notice" data-tone="error" role="alert">
          {{ t('admin_user_detail.erase_step2') }}
        </p>
        <button
          type="button"
          class="fh-btn fh-btn-danger"
          :disabled="erasing || isErased"
          @click="onErase"
        >
          {{
            isErased
              ? t('admin_user_detail.already_erased')
              : eraseStep === 0
                ? t('admin_user_detail.erase')
                : eraseStep === 1
                  ? t('admin_user_detail.erase_confirm_1')
                  : t('admin_user_detail.erase_confirm_final')
          }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}

.meta-line {
  display: inline-flex;
  align-items: baseline;
  gap: var(--fh-space-3);
  color: var(--fh-subtle);
}

.role {
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
}

.section-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  margin: var(--fh-space-3) 0;
}

.edit-form {
  max-width: 480px;
}

.checkbox {
  display: flex;
  gap: var(--fh-space-2);
  align-items: center;
  padding: var(--fh-space-2) 0;
}

.cb-label {
  color: var(--fh-ink);
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
  align-items: center;
}

.security-row {
  display: flex;
  justify-content: space-between;
  gap: var(--fh-space-4);
  align-items: flex-start;
}

.change-email-form {
  margin-top: var(--fh-space-4);
  max-width: 480px;
}

.reset-box {
  margin-top: var(--fh-space-3);
  padding: var(--fh-space-4);
  background: var(--fh-accent-soft);
  border: var(--fh-border);
  border-left: 2px solid var(--fh-accent);
  border-radius: var(--fh-radius-sm);
}

.reset-eyebrow {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--fh-subtle);
}

.warning {
  margin: var(--fh-space-2) 0;
}

.token {
  background: var(--fh-paper);
  padding: var(--fh-space-3);
  border: var(--fh-border);
  word-break: break-all;
  user-select: all;
}

.danger-zone {
  margin-top: var(--fh-space-4);
}

.sessions-head,
.files-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--fh-space-4);
}

.revoke-btn {
  color: var(--fh-accent);
  white-space: nowrap;
}

.file-list {
  list-style: none;
  padding: 0;
  margin: var(--fh-space-2) 0 0;
}

.file-item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fh-space-3);
  padding: var(--fh-space-2) 0;
  border-top: 1px solid var(--fh-hairline);
}

.file-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.email-link {
  text-decoration: none;
  color: inherit;
}

.email-link:hover .file-name {
  color: var(--fh-accent);
}

.file-name {
  color: var(--fh-ink);
}

.file-meta {
  display: inline-flex;
  align-items: baseline;
  gap: var(--fh-space-3);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.session-list {
  list-style: none;
  padding: 0;
  margin: var(--fh-space-2) 0 0;
}

.session-item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fh-space-3);
  padding: var(--fh-space-2) 0;
  border-top: 1px solid var(--fh-hairline);
}

.session-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.session-ua {
  color: var(--fh-ink);
}

.session-meta {
  display: inline-flex;
  gap: var(--fh-space-3);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.empty {
  margin: var(--fh-space-2) 0;
}
</style>
