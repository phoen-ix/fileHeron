<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import {
  eraseUser,
  forcePasswordReset,
  getUser,
  updateUser,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type { AdminUserItem, UserRole } from '@/types/api'

const route = useRoute()
const router = useRouter()
const { t, locale } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const user = ref<AdminUserItem | null>(null)
const loading = ref(true)

const editName = ref('')
const editRole = ref<UserRole>('client')
const editQuota = ref<number | null>(null)
const editDisabled = ref(false)
const saving = ref(false)
const savingError = ref<string | null>(null)

const resetting = ref(false)
const resetTokenPlaintext = ref<string | null>(null)

const eraseStep = ref<0 | 1 | 2>(0)
const erasing = ref(false)

const isErased = computed(() => user.value?.email === '[erased]')

async function load() {
  loading.value = true
  try {
    const { data } = await getUser(Number(route.params.id))
    user.value = data
    editName.value = data.display_name
    editRole.value = data.role
    editQuota.value = data.quota_bytes
    editDisabled.value = data.is_disabled
  } finally {
    loading.value = false
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
    if (editQuota.value !== user.value.quota_bytes) payload.quota_bytes = editQuota.value
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
  if (!confirm(t('admin_user_detail.force_reset_confirm'))) return
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

async function onErase() {
  if (!user.value) return
  if (eraseStep.value === 0) {
    eraseStep.value = 1
    return
  }
  if (eraseStep.value === 1) {
    eraseStep.value = 2
    return
  }
  erasing.value = true
  try {
    const { data } = await eraseUser(user.value.id)
    ui.pushToast(
      t('admin_user_detail.erased_toast', {
        n: data.deleted_files,
        bytes: data.deleted_bytes,
      }),
      'success',
    )
    await router.push({ name: 'admin-users' })
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    erasing.value = false
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(
    locale.value === 'de' ? 'de-AT' : 'en-US',
    { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' },
  )
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <template v-else-if="user">
      <span class="fh-eyebrow">{{ t('admin_user_detail.eyebrow') }}</span>
      <h1 class="fh-display-md">{{ user.display_name }}</h1>
      <p class="meta-line">
        <span class="fh-mono">{{ user.email }}</span>
        <span class="fh-mono role">{{ user.role }}</span>
        <span v-if="user.has_2fa" class="fh-pill" data-state="active">2FA on</span>
        <span v-else class="fh-pill">2FA off</span>
        <span v-if="user.is_disabled" class="fh-pill" data-state="danger">disabled</span>
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
        <div v-if="savingError" class="fh-notice" data-tone="error">{{ savingError }}</div>
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

      <hr class="fh-rule" />

      <div class="danger-zone">
        <h2 class="section-h2">{{ t('admin_user_detail.danger') }}</h2>
        <p class="fh-field-help">{{ t('admin_user_detail.erase_help') }}</p>
        <p v-if="eraseStep === 1" class="fh-notice" data-tone="error">
          {{ t('admin_user_detail.erase_step1') }}
        </p>
        <p v-if="eraseStep === 2" class="fh-notice" data-tone="error">
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
</style>
