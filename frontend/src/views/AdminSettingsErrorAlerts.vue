<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getErrorAlertSettings, updateErrorAlertSettings } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)

const enabled = ref(false)
const sourceHttp5xx = ref(true)
const recipientsMode = ref<'admins' | 'custom'>('admins')
const customRecipientsText = ref('')
const cooldownMinutes = ref(15)
const maxPerHour = ref(20)

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const parsedRecipients = computed<string[]>(() =>
  customRecipientsText.value
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0),
)

const invalidRecipients = computed<string[]>(() =>
  parsedRecipients.value.filter((addr) => !EMAIL_RE.test(addr)),
)

const validationError = computed<string | null>(() => {
  if (!enabled.value) return null
  if (cooldownMinutes.value < 1 || cooldownMinutes.value > 1440)
    return t('admin_error_alerts.err_cooldown_range')
  if (maxPerHour.value < 1 || maxPerHour.value > 1000)
    return t('admin_error_alerts.err_cap_range')
  if (recipientsMode.value === 'custom') {
    if (parsedRecipients.value.length === 0)
      return t('admin_error_alerts.err_custom_empty')
    if (invalidRecipients.value.length > 0)
      return t('admin_error_alerts.err_bad_email', { list: invalidRecipients.value.join(', ') })
  }
  return null
})

const canSave = computed(() => !saving.value && validationError.value === null)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getErrorAlertSettings()
    enabled.value = data.enabled
    sourceHttp5xx.value = data.source_http_5xx
    recipientsMode.value = data.recipients_mode
    customRecipientsText.value = data.custom_recipients.join('\n')
    cooldownMinutes.value = data.cooldown_minutes
    maxPerHour.value = data.max_per_hour
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onSave() {
  if (!canSave.value) return
  saving.value = true
  errorMsg.value = null
  try {
    const { data } = await updateErrorAlertSettings({
      enabled: enabled.value,
      source_http_5xx: sourceHttp5xx.value,
      recipients_mode: recipientsMode.value,
      custom_recipients: parsedRecipients.value,
      cooldown_minutes: cooldownMinutes.value,
      max_per_hour: maxPerHour.value,
    })
    enabled.value = data.enabled
    sourceHttp5xx.value = data.source_http_5xx
    recipientsMode.value = data.recipients_mode
    customRecipientsText.value = data.custom_recipients.join('\n')
    cooldownMinutes.value = data.cooldown_minutes
    maxPerHour.value = data.max_per_hour
    ui.pushToast(t('admin_error_alerts.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="policy-page" data-density="operator">
    <span class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_error_alerts.title') }}
    </span>

    <p class="fh-field-help intro">{{ t('admin_error_alerts.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="policy-form" @submit.prevent="onSave">
      <fieldset class="toggle-fieldset">
        <label class="toggle">
          <input v-model="enabled" type="checkbox" />
          <span>{{ t('admin_error_alerts.enabled_label') }}</span>
        </label>
        <p class="fh-field-help">{{ t('admin_error_alerts.enabled_help') }}</p>
      </fieldset>

      <template v-if="enabled">
        <fieldset class="toggle-fieldset">
          <label class="toggle">
            <input v-model="sourceHttp5xx" type="checkbox" />
            <span>{{ t('admin_error_alerts.http_label') }}</span>
          </label>
          <p class="fh-field-help">{{ t('admin_error_alerts.http_help') }}</p>
          <p class="fh-field-help cron-note">{{ t('admin_error_alerts.cron_note') }}</p>
        </fieldset>

        <fieldset class="toggle-fieldset">
          <legend class="legend">{{ t('admin_error_alerts.recipients_label') }}</legend>
          <label class="toggle">
            <input v-model="recipientsMode" type="radio" value="admins" />
            <span>{{ t('admin_error_alerts.recipients_admins') }}</span>
          </label>
          <label class="toggle">
            <input v-model="recipientsMode" type="radio" value="custom" />
            <span>{{ t('admin_error_alerts.recipients_custom') }}</span>
          </label>
          <template v-if="recipientsMode === 'custom'">
            <textarea
              v-model="customRecipientsText"
              class="fh-input recipients-area"
              rows="4"
              :placeholder="t('admin_error_alerts.recipients_placeholder')"
            />
            <p class="fh-field-help">{{ t('admin_error_alerts.recipients_help') }}</p>
          </template>
        </fieldset>

        <fieldset class="toggle-fieldset">
          <legend class="legend">{{ t('admin_error_alerts.throttle_label') }}</legend>
          <div class="num-row">
            <label class="num-field">
              <span>{{ t('admin_error_alerts.cooldown_label') }}</span>
              <input v-model.number="cooldownMinutes" type="number" class="fh-input" min="1" max="1440" />
            </label>
            <label class="num-field">
              <span>{{ t('admin_error_alerts.cap_label') }}</span>
              <input v-model.number="maxPerHour" type="number" class="fh-input" min="1" max="1000" />
            </label>
          </div>
          <p class="fh-field-help">{{ t('admin_error_alerts.throttle_help') }}</p>
        </fieldset>
      </template>

      <div v-if="validationError" class="fh-notice" data-tone="warning">{{ validationError }}</div>
      <div v-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="!canSave">
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

.toggle-fieldset {
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.legend {
  font-weight: 600;
  padding: 0;
  color: var(--fh-ink);
}

.toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-2);
  cursor: pointer;
}

.recipients-area {
  width: 100%;
  font-family: var(--fh-font-mono, monospace);
  resize: vertical;
}

.num-row {
  display: flex;
  gap: var(--fh-space-4);
  flex-wrap: wrap;
}

.num-field {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
}

.num-field input {
  width: 8rem;
}

.cron-note {
  font-style: italic;
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
}
</style>
