<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getEmailChangePolicy, updateEmailChangePolicy } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type {
  EmailChangeOidcMode,
  EmailChangePolicyResponse,
  EmailChangeVerificationMode,
} from '@/types/api'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()
const auth = useAuthStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)

const verificationMode = ref<EmailChangeVerificationMode>('verify_new')
const selfService = ref(false)
const oidcMode = ref<EmailChangeOidcMode>('reset_setpw')

const VERIFICATION_MODES: EmailChangeVerificationMode[] = [
  'verify_new',
  'verify_both',
  'immediate',
]
const OIDC_MODES: EmailChangeOidcMode[] = ['reset_setpw', 'reset_only', 'keep']

function applyResponse(data: EmailChangePolicyResponse) {
  verificationMode.value = data.verification_mode
  selfService.value = data.self_service
  oidcMode.value = data.oidc_mode
}

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getEmailChangePolicy()
    applyResponse(data)
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onSave() {
  saving.value = true
  errorMsg.value = null
  try {
    const { data } = await updateEmailChangePolicy({
      verification_mode: verificationMode.value,
      self_service: selfService.value,
      oidc_mode: oidcMode.value,
    })
    applyResponse(data)
    // The self-service flag drives `can_change_own_email` on /me, which the
    // Account page reads to show/hide its email-change block.
    await auth.refreshMe()
    ui.pushToast(t('admin_settings_email_change.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="policy-page" data-density="operator">
    <span class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_settings_email_change.title') }}
    </span>

    <p class="fh-field-help intro">{{ t('admin_settings_email_change.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="policy-form" @submit.prevent="onSave">
      <!-- Verification mode -->
      <fieldset class="opt-fieldset">
        <legend class="fh-field-label">
          {{ t('admin_settings_email_change.mode_label') }}
        </legend>
        <label
          v-for="m in VERIFICATION_MODES"
          :key="m"
          class="opt-radio"
        >
          <input v-model="verificationMode" type="radio" :value="m" />
          <span class="opt-text">
            <span class="opt-name">{{ t(`admin_settings_email_change.mode.${m}`) }}</span>
            <span class="fh-field-help">{{ t(`admin_settings_email_change.mode_help.${m}`) }}</span>
          </span>
        </label>
      </fieldset>

      <!-- SSO-bound behaviour -->
      <fieldset class="opt-fieldset">
        <legend class="fh-field-label">
          {{ t('admin_settings_email_change.oidc_label') }}
        </legend>
        <label
          v-for="m in OIDC_MODES"
          :key="m"
          class="opt-radio"
        >
          <input v-model="oidcMode" type="radio" :value="m" />
          <span class="opt-text">
            <span class="opt-name">{{ t(`admin_settings_email_change.oidc.${m}`) }}</span>
            <span class="fh-field-help">{{ t(`admin_settings_email_change.oidc_help.${m}`) }}</span>
          </span>
        </label>
      </fieldset>

      <!-- Self-service toggle -->
      <fieldset class="toggle-fieldset">
        <label class="toggle">
          <input v-model="selfService" type="checkbox" />
          <span>{{ t('admin_settings_email_change.self_service_label') }}</span>
        </label>
        <p class="fh-field-help">{{ t('admin_settings_email_change.self_service_help') }}</p>
      </fieldset>

      <div v-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.policy-page {
  max-width: 720px;
}

.intro {
  margin: var(--fh-space-2) 0 var(--fh-space-3);
  max-width: 64ch;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-4) 0;
}

.policy-form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-4);
  margin-top: var(--fh-space-3);
}

.opt-fieldset,
.toggle-fieldset {
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.opt-radio {
  display: flex;
  align-items: flex-start;
  gap: var(--fh-space-2);
  cursor: pointer;
}

.opt-radio input {
  margin-top: 4px;
}

.opt-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.opt-name {
  color: var(--fh-ink);
}

.toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-2);
  cursor: pointer;
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
}
</style>
