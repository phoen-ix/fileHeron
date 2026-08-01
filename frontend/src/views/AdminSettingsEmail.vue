<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  getEmailSettings,
  testEmailSend,
  updateEmailSettings,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type {
  EmailSettingsResponse,
  SmtpTlsMode,
  TestEmailResponse,
  UpdateEmailSettingsRequest,
} from '@/types/api'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const errorMsg = ref<string | null>(null)
const current = ref<EmailSettingsResponse | null>(null)
const testResult = ref<TestEmailResponse | null>(null)
const testTo = ref('')
// Most MTAs require SMTP AUTH. We block save/test on blank credentials
// unless the admin explicitly opts into an anonymous (no-auth) relay.
const allowAnonymous = ref(false)

interface FormState {
  host: string
  port: number | null
  user: string
  password: string
  from_email: string
  from_name: string
  tls_mode: SmtpTlsMode
  helo_hostname: string
}

const form = ref<FormState>({
  host: '',
  port: 587,
  user: '',
  password: '',
  from_email: '',
  from_name: '',
  tls_mode: 'starttls',
  helo_hostname: '',
})

const tlsOptions: { value: SmtpTlsMode; labelKey: string; helpKey: string }[] = [
  {
    value: 'starttls',
    labelKey: 'admin_email.tls.starttls',
    helpKey: 'admin_email.tls.starttls_help',
  },
  {
    value: 'implicit',
    labelKey: 'admin_email.tls.implicit',
    helpKey: 'admin_email.tls.implicit_help',
  },
  {
    value: 'none',
    labelKey: 'admin_email.tls.none',
    helpKey: 'admin_email.tls.none_help',
  },
]

const passwordPlaceholder = computed(() =>
  current.value?.is_password_set
    ? t('admin_email.password_set_placeholder')
    : t('admin_email.password_unset_placeholder'),
)

// Auth is "configured" when a username is present AND a password is either
// typed now or already stored (blank password = keep existing).
const authConfigured = computed(
  () =>
    form.value.user.trim() !== '' &&
    (form.value.password !== '' || current.value?.is_password_set === true),
)
// Save/test are blocked when credentials are incomplete and the admin
// hasn't ticked the anonymous-relay escape hatch.
const authBlocked = computed(() => !allowAnonymous.value && !authConfigured.value)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getEmailSettings()
    current.value = data
    form.value.host = data.host
    form.value.port = data.port
    form.value.user = data.user
    form.value.password = ''
    form.value.from_email = data.from_email
    form.value.from_name = data.from_name
    form.value.tls_mode = data.tls_mode
    form.value.helo_hostname = data.helo_hostname
    // Pre-tick "allow anonymous" only for an existing, already-saved config
    // that deliberately runs without a username, so we don't retroactively
    // block it. Fresh/unconfigured setups stay unchecked → creds required.
    allowAnonymous.value = data.is_configured && data.user.trim() === ''
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

function buildPayload(): UpdateEmailSettingsRequest {
  return {
    host: form.value.host,
    port: form.value.port ?? undefined,
    user: form.value.user,
    // null = keep existing; only include when admin actually typed.
    password: form.value.password === '' ? null : form.value.password,
    from_email: form.value.from_email,
    from_name: form.value.from_name,
    tls_mode: form.value.tls_mode,
    helo_hostname: form.value.helo_hostname,
  }
}

async function onSave() {
  if (authBlocked.value) {
    errorMsg.value = t('admin_email.auth_required_note')
    return
  }
  saving.value = true
  errorMsg.value = null
  try {
    await updateEmailSettings(buildPayload())
    ui.pushToast(t('admin_email.saved_toast'), 'success')
    await load()
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

async function onTest() {
  if (authBlocked.value) {
    testResult.value = {
      ok: false,
      error_class: 'AuthRequired',
      error_message: t('admin_email.auth_required_note'),
      smtp_code: null,
      hint: null,
    }
    return
  }
  if (!testTo.value) {
    testResult.value = {
      ok: false,
      error_class: 'NoRecipient',
      error_message: t('admin_email.test_recipient_required'),
      smtp_code: null,
      hint: null,
    }
    return
  }
  testing.value = true
  testResult.value = null
  try {
    const { data } = await testEmailSend({
      to: testTo.value,
      override: buildPayload(),
    })
    testResult.value = data
  } catch (err) {
    testResult.value = {
      ok: false,
      error_class: 'RequestError',
      error_message: describe(err),
      smtp_code: null,
      hint: null,
    }
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="email-page" data-density="operator">
    <h1 class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_email.title') }}
    </h1>

    <p class="fh-field-help intro">{{ t('admin_email.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <template v-else-if="current">
      <div class="status-row">
        <span
          class="fh-pill"
          :data-state="current.is_configured ? 'active' : undefined"
        >
          {{
            current.is_configured
              ? t('admin_email.status_configured')
              : t('admin_email.status_logs_fallback')
          }}
        </span>
        <span v-if="!current.has_db_overrides" class="fh-mono env-hint">
          {{ t('admin_email.env_only') }}
        </span>
      </div>

      <form class="email-form" @submit.prevent="onSave">
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_email.host_label') }}</span>
          <input
            v-model.trim="form.host"
            class="fh-field-input fh-field-mono"
            type="text"
            placeholder="smtp.example.com"
          />
          <span class="fh-field-help">{{ t('admin_email.host_help') }}</span>
        </label>

        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_email.port_label') }}</span>
          <input
            v-model.number="form.port"
            class="fh-field-input fh-field-mono"
            type="number"
            min="1"
            max="65535"
          />
        </label>

        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_email.helo_label') }}</span>
          <input
            v-model.trim="form.helo_hostname"
            class="fh-field-input fh-field-mono"
            type="text"
            autocomplete="off"
            placeholder="mail.example.com"
          />
          <span class="fh-field-help">{{ t('admin_email.helo_help') }}</span>
        </label>

        <fieldset class="tls-fieldset">
          <legend class="fh-field-label">{{ t('admin_email.tls_label') }}</legend>
          <label v-for="opt in tlsOptions" :key="opt.value" class="tls-option">
            <input v-model="form.tls_mode" type="radio" :value="opt.value" />
            <span>
              <span class="tls-name">{{ t(opt.labelKey) }}</span>
              <span class="tls-help">{{ t(opt.helpKey) }}</span>
            </span>
          </label>
        </fieldset>

        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_email.user_label') }}</span>
          <input
            v-model.trim="form.user"
            class="fh-field-input fh-field-mono"
            type="text"
            autocomplete="off"
          />
        </label>

        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_email.password_label') }}</span>
          <input
            v-model="form.password"
            class="fh-field-input fh-field-mono"
            type="password"
            autocomplete="off"
            :placeholder="passwordPlaceholder"
          />
          <span class="fh-field-help">{{ t('admin_email.password_help') }}</span>
        </label>

        <label class="anon-option">
          <input v-model="allowAnonymous" type="checkbox" />
          <span>
            <span class="tls-name">{{ t('admin_email.allow_anonymous_label') }}</span>
            <span class="tls-help">{{ t('admin_email.allow_anonymous_help') }}</span>
          </span>
        </label>

        <div v-if="authBlocked" class="fh-notice" data-tone="accent">
          {{ t('admin_email.auth_required_note') }}
        </div>

        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_email.from_email_label') }}</span>
          <input
            v-model.trim="form.from_email"
            class="fh-field-input fh-field-mono"
            type="email"
            placeholder="noreply@example.com"
          />
        </label>

        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_email.from_name_label') }}</span>
          <input
            v-model.trim="form.from_name"
            class="fh-field-input"
            type="text"
            maxlength="120"
          />
        </label>

        <div
v-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>

        <div class="actions">
          <button type="submit" class="fh-btn" :disabled="saving || authBlocked">
            {{ saving ? t('common.loading') : t('common.save') }}
          </button>
        </div>
      </form>

      <hr class="fh-rule" />

      <section class="test-section">
        <h2 class="form-h2">{{ t('admin_email.test_heading') }}</h2>
        <p class="fh-field-help">{{ t('admin_email.test_help') }}</p>

        <div class="test-row">
          <input
            v-model.trim="testTo"
            :aria-label="t('admin_email.test_placeholder')"
            class="fh-field-input fh-field-mono"
            type="email"
            :placeholder="t('admin_email.test_placeholder')"
            :disabled="testing"
          />
          <button
            type="button"
            class="fh-btn"
            :disabled="testing || authBlocked"
            @click="onTest"
          >
            {{ testing ? t('common.loading') : t('admin_email.test_button') }}
          </button>
        </div>

        <div
          v-if="testResult"
          class="fh-notice"
          :data-tone="testResult.ok ? 'success' : 'error'"
        >
          <strong v-if="testResult.ok">{{ t('admin_email.test_ok') }}</strong>
          <strong v-else>{{ t('admin_email.test_failed') }}</strong>
          <div v-if="!testResult.ok" class="test-detail fh-mono">
            <div>{{ testResult.error_class }}</div>
            <div v-if="testResult.smtp_code !== null">
              SMTP {{ testResult.smtp_code }}
            </div>
            <div>{{ testResult.error_message }}</div>
          </div>
          <div v-if="!testResult.ok && testResult.hint" class="test-hint">
            <strong>{{ t('admin_email.hint_label') }}</strong> {{ testResult.hint }}
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.email-page {
  max-width: none;
}

.intro {
  margin: var(--fh-space-2) 0 var(--fh-space-3);
  max-width: 64ch;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-4) 0;
}

.status-row {
  display: flex;
  gap: var(--fh-space-3);
  align-items: center;
  margin-bottom: var(--fh-space-4);
}

.env-hint {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.email-form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.tls-fieldset {
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.tls-option,
.anon-option {
  display: flex;
  align-items: flex-start;
  gap: var(--fh-space-2);
  cursor: pointer;
}

.tls-option > span,
.anon-option > span {
  display: flex;
  flex-direction: column;
}

.tls-name {
  font-weight: 500;
}

.tls-help {
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-3);
}

.test-section {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  margin-top: var(--fh-space-4);
}

.form-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.25rem;
  margin: 0;
}

.test-row {
  display: flex;
  gap: var(--fh-space-3);
  align-items: stretch;
}

.test-row input {
  flex: 1;
}

.test-detail {
  margin-top: var(--fh-space-2);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-ink-soft);
  word-break: break-all;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.test-hint {
  margin-top: var(--fh-space-2);
  font-size: var(--fh-text-body-sm);
  color: var(--fh-ink-soft);
}
</style>
