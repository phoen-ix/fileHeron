<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import {
  createProvider,
  deleteProvider,
  getProvider,
  listPresets,
  testDiscovery,
  testProviderConnection,
  updateProvider,
  type OIDCPreset,
  type OIDCProviderItem,
  type PresetMeta,
  type TestConnectionResponse,
} from '@/api/settings'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()
const route = useRoute()
const router = useRouter()

const isEdit = computed(() => typeof route.params.id === 'string' && route.params.id.length > 0)
const providerId = computed(() => (route.params.id as string) || '')

const presets = ref<PresetMeta[]>([])
const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const deleting = ref(false)
const testResult = ref<TestConnectionResponse | null>(null)
const errorMsg = ref<string | null>(null)
const current = ref<OIDCProviderItem | null>(null)

interface FormState {
  name: string
  preset: OIDCPreset
  issuer_url: string
  client_id: string
  client_secret: string
  groups_claim: string
  admin_groups: string
  employee_groups: string
  redirect_uri: string
  enabled: boolean
}

const form = ref<FormState>({
  name: '',
  preset: 'custom',
  issuer_url: '',
  client_id: '',
  client_secret: '',
  groups_claim: 'groups',
  admin_groups: '',
  employee_groups: '',
  redirect_uri: '',
  enabled: true,
})

// Preset helper inputs (tenant/host/realm/slug). Bound to the form
// fields so changing them triggers a re-render of issuer_url.
const helperFields = ref<Record<string, string>>({})

const activePreset = computed<PresetMeta | null>(() => {
  return presets.value.find((p) => p.preset === form.value.preset) ?? null
})

function applyPresetDefaults() {
  const p = activePreset.value
  if (!p) return
  if (!form.value.groups_claim || form.value.groups_claim === 'groups') {
    form.value.groups_claim = p.default_groups_claim
  }
  // Reset helper fields when switching presets.
  helperFields.value = Object.fromEntries(
    p.issuer_template_fields.map((f) => [f.key, '']),
  )
  // For Google (fixed issuer), populate it directly.
  if (p.issuer && !form.value.issuer_url) {
    form.value.issuer_url = p.issuer
  }
}

watch(
  () => form.value.preset,
  () => {
    applyPresetDefaults()
    rebuildIssuerFromHelpers()
  },
)

watch(
  helperFields,
  () => {
    rebuildIssuerFromHelpers()
  },
  { deep: true },
)

function rebuildIssuerFromHelpers() {
  const p = activePreset.value
  if (!p) return
  if (p.issuer) {
    form.value.issuer_url = p.issuer
    return
  }
  if (!p.issuer_template) return
  // Only auto-rebuild if every required helper has a value, otherwise
  // leave whatever the user typed manually.
  const allFilled = p.issuer_template_fields.every(
    (f) => helperFields.value[f.key] && helperFields.value[f.key].trim() !== '',
  )
  if (!allFilled) return
  let url = p.issuer_template
  for (const f of p.issuer_template_fields) {
    url = url.replaceAll(`{${f.key}}`, encodeURIComponent(helperFields.value[f.key]))
  }
  form.value.issuer_url = url
}

async function loadPresets() {
  const { data } = await listPresets()
  presets.value = data.presets
}

async function loadProvider() {
  const { data } = await getProvider(providerId.value)
  current.value = data
  form.value.name = data.name
  form.value.preset = data.preset
  form.value.issuer_url = data.issuer_url
  form.value.client_id = data.client_id
  form.value.client_secret = ''
  form.value.groups_claim = data.groups_claim
  form.value.admin_groups = data.admin_groups
  form.value.employee_groups = data.employee_groups
  form.value.redirect_uri = data.redirect_uri
  form.value.enabled = data.enabled
}

async function init() {
  loading.value = true
  errorMsg.value = null
  try {
    await loadPresets()
    if (isEdit.value) {
      await loadProvider()
    } else {
      applyPresetDefaults()
    }
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onTest() {
  testing.value = true
  testResult.value = null
  try {
    const issuer = form.value.issuer_url.trim()
    if (isEdit.value) {
      const { data } = await testProviderConnection(providerId.value, {
        issuer_url: issuer || undefined,
      })
      testResult.value = data
    } else {
      const { data } = await testDiscovery({ issuer_url: issuer || undefined })
      testResult.value = data
    }
  } catch (err) {
    testResult.value = { ok: false, error: describe(err) }
  } finally {
    testing.value = false
  }
}

async function onSave() {
  saving.value = true
  errorMsg.value = null
  try {
    if (isEdit.value) {
      await updateProvider(providerId.value, {
        name: form.value.name,
        preset: form.value.preset,
        issuer_url: form.value.issuer_url,
        client_id: form.value.client_id,
        // null = leave unchanged.
        client_secret:
          form.value.client_secret === '' ? null : form.value.client_secret,
        groups_claim: form.value.groups_claim,
        admin_groups: form.value.admin_groups,
        employee_groups: form.value.employee_groups,
        redirect_uri: form.value.redirect_uri,
        enabled: form.value.enabled,
      })
      ui.pushToast(t('admin_sso_edit.saved_toast'), 'success')
      await loadProvider()
    } else {
      if (!form.value.client_secret) {
        errorMsg.value = t('admin_sso_edit.secret_required_on_create')
        return
      }
      const { data } = await createProvider({
        name: form.value.name,
        preset: form.value.preset,
        issuer_url: form.value.issuer_url,
        client_id: form.value.client_id,
        client_secret: form.value.client_secret,
        groups_claim: form.value.groups_claim,
        admin_groups: form.value.admin_groups,
        employee_groups: form.value.employee_groups,
        redirect_uri: form.value.redirect_uri,
        enabled: form.value.enabled,
      })
      ui.pushToast(t('admin_sso_edit.created_toast'), 'success')
      await router.replace({ name: 'admin-settings-sso-edit', params: { id: data.id } })
    }
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

async function onDelete() {
  if (!isEdit.value) return
  if (!(await ui.confirm({ message: t('admin_sso_edit.delete_confirm'), danger: true }))) return
  deleting.value = true
  errorMsg.value = null
  try {
    await deleteProvider(providerId.value)
    ui.pushToast(t('admin_sso_edit.deleted_toast'), 'success')
    await router.replace({ name: 'admin-settings-sso' })
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    deleting.value = false
  }
}

onMounted(init)
</script>

<template>
  <div class="sso-edit" data-density="operator">
    <span class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_sso_list.title') }} / {{ isEdit ? t('admin_sso_edit.heading_edit') : t('admin_sso_edit.heading_new') }}
    </span>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="sso-form" @submit.prevent="onSave">
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_sso_edit.name') }}</span>
        <input
          v-model.trim="form.name"
          class="fh-field-input"
          type="text"
          required
          :placeholder="t('admin_sso_edit.name_placeholder')"
        />
        <span class="fh-field-help">{{ t('admin_sso_edit.name_help') }}</span>
      </label>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_sso_edit.preset') }}</span>
        <select v-model="form.preset" class="fh-field-input">
          <option v-for="p in presets" :key="p.preset" :value="p.preset">
            {{ p.label }}
          </option>
        </select>
        <span v-if="activePreset?.notes" class="fh-field-help">
          {{ activePreset.notes }}
        </span>
      </label>

      <!-- Helper inputs for issuer template (tenant/host/realm/slug) -->
      <template
        v-if="activePreset && activePreset.issuer_template_fields.length > 0"
      >
        <label
          v-for="hf in activePreset.issuer_template_fields"
          :key="hf.key"
          class="fh-field"
        >
          <span class="fh-field-label">{{ hf.label }}</span>
          <input
            v-model.trim="helperFields[hf.key]"
            class="fh-field-input fh-field-mono"
            type="text"
            :placeholder="hf.placeholder"
          />
        </label>
      </template>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_sso_edit.issuer_url') }}</span>
        <input
          v-model.trim="form.issuer_url"
          class="fh-field-input fh-field-mono"
          type="url"
          required
          placeholder="https://idp.example.com/realms/fileheron"
        />
        <span class="fh-field-help">{{ t('admin_sso_edit.issuer_url_help') }}</span>
      </label>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_sso_edit.client_id') }}</span>
        <input
          v-model.trim="form.client_id"
          class="fh-field-input fh-field-mono"
          type="text"
          required
        />
      </label>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_sso_edit.client_secret') }}</span>
        <input
          v-model="form.client_secret"
          class="fh-field-input fh-field-mono"
          type="password"
          autocomplete="off"
          :placeholder="
            isEdit && current?.client_secret_set
              ? t('admin_sso_edit.secret_set_placeholder')
              : t('admin_sso_edit.secret_unset_placeholder')
          "
        />
        <span class="fh-field-help">{{ t('admin_sso_edit.client_secret_help') }}</span>
      </label>

      <!-- Group mapping fields: hidden for presets that don't support it -->
      <template v-if="activePreset?.supports_groups">
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_sso_edit.groups_claim') }}</span>
          <input
            v-model.trim="form.groups_claim"
            class="fh-field-input fh-field-mono"
            type="text"
          />
          <span class="fh-field-help">{{ t('admin_sso_edit.groups_claim_help') }}</span>
        </label>

        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_sso_edit.admin_groups') }}</span>
          <input
            v-model.trim="form.admin_groups"
            class="fh-field-input fh-field-mono"
            type="text"
            placeholder="fh-admins"
          />
        </label>

        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_sso_edit.employee_groups') }}</span>
          <input
            v-model.trim="form.employee_groups"
            class="fh-field-input fh-field-mono"
            type="text"
            placeholder="fh-employees"
          />
        </label>
      </template>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_sso_edit.redirect_uri') }}</span>
        <input
          v-model.trim="form.redirect_uri"
          class="fh-field-input fh-field-mono"
          type="url"
          :placeholder="t('admin_sso_edit.redirect_uri_default')"
        />
        <span class="fh-field-help">{{ t('admin_sso_edit.redirect_uri_help') }}</span>
      </label>

      <label class="fh-field fh-field--inline">
        <input v-model="form.enabled" type="checkbox" />
        <span class="fh-field-label">{{ t('admin_sso_edit.enabled') }}</span>
      </label>

      <div
        v-if="testResult"
        class="fh-notice"
        :data-tone="testResult.ok ? 'success' : 'error'"
      >
        <strong v-if="testResult.ok">{{ t('admin_sso_edit.test_ok') }}</strong>
        <strong v-else>{{ t('admin_sso_edit.test_failed') }}</strong>
        <div v-if="testResult.ok" class="test-detail fh-mono">
          <div>issuer: {{ testResult.issuer }}</div>
          <div>auth: {{ testResult.authorization_endpoint }}</div>
          <div>token: {{ testResult.token_endpoint }}</div>
        </div>
        <div v-else class="test-detail fh-mono">{{ testResult.error }}</div>
      </div>

      <div v-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
        <button
          type="button"
          class="fh-btn-ghost fh-btn"
          :disabled="testing"
          @click="onTest"
        >
          {{ testing ? t('common.loading') : t('admin_sso_edit.test_button') }}
        </button>
        <button
          v-if="isEdit"
          type="button"
          class="fh-btn-ghost fh-btn fh-btn-danger"
          :disabled="deleting"
          @click="onDelete"
        >
          {{ deleting ? t('common.loading') : t('admin_sso_edit.delete_button') }}
        </button>
        <button
          type="button"
          class="fh-btn-ghost fh-btn"
          @click="router.push({ name: 'admin-settings-sso' })"
        >
          {{ t('common.cancel') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.sso-edit {
  max-width: none;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-4) 0;
}

.sso-form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  margin-top: var(--fh-space-3);
}

.fh-field--inline {
  flex-direction: row;
  align-items: center;
  gap: var(--fh-space-2);
}

.test-detail {
  margin-top: var(--fh-space-2);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-ink-soft);
  word-break: break-all;
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-4);
  flex-wrap: wrap;
}

.fh-btn-danger {
  color: var(--fh-danger, #b91c1c);
}
</style>
