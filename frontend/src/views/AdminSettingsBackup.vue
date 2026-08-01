<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  exportConfigBackup,
  importConfigBackup,
  previewBackupImport,
  type BackupCategory,
  type BackupImportSummary,
  type BackupSecretMode,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import { downloadBlob } from '@/utils/downloadBlob'

const { t } = useI18n()
const { describe, describeBlob } = useApiError()
const ui = useUiStore()

const CATEGORY_KEYS: BackupCategory[] = [
  'settings_branding',
  'oidc_webhooks',
  'groups',
  'users',
  'logs',
]
const MIN_PASSPHRASE = 12

// --- export state ---------------------------------------------------------
const selected = ref<Record<BackupCategory, boolean>>({
  settings_branding: true,
  oidc_webhooks: true,
  groups: true,
  users: true,
  logs: false,
})
const secretMode = ref<BackupSecretMode>('passphrase')
const passphrase = ref('')
const passphraseConfirm = ref('')
const includeEnv = ref(false)
const exporting = ref(false)

const anySelected = computed(() => CATEGORY_KEYS.some((k) => selected.value[k]))
const passphraseOk = computed(
  () =>
    secretMode.value !== 'passphrase' ||
    (passphrase.value.length >= MIN_PASSPHRASE && passphrase.value === passphraseConfirm.value),
)
const canExport = computed(() => anySelected.value && passphraseOk.value && !exporting.value)

function onSecretModeChange() {
  if (secretMode.value !== 'passphrase') {
    includeEnv.value = false
    passphrase.value = ''
    passphraseConfirm.value = ''
  }
}

async function onExport() {
  if (!canExport.value) return
  exporting.value = true
  try {
    const categories = CATEGORY_KEYS.filter((k) => selected.value[k])
    const { data } = await exportConfigBackup({
      categories,
      secret_mode: secretMode.value,
      passphrase: secretMode.value === 'passphrase' ? passphrase.value : null,
      include_env: includeEnv.value,
    })
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')
    downloadBlob(data as Blob, `fileheron-config-${stamp}.fhbackup.json`)
    ui.pushToast(t('admin_backup.export_done'), 'success')
  } catch (err) {
    ui.pushToast(await describeBlob(err), 'error')
  } finally {
    exporting.value = false
  }
}

// --- import state ---------------------------------------------------------
const importFile = ref<File | null>(null)
const importPassphrase = ref('')
const previewing = ref(false)
const importing = ref(false)
const preview = ref<BackupImportSummary | null>(null)
const importError = ref<string | null>(null)

function onPickFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0] ?? null
  importFile.value = f
  preview.value = null
  importError.value = null
}

async function onPreview() {
  if (!importFile.value) return
  previewing.value = true
  importError.value = null
  preview.value = null
  try {
    const { data } = await previewBackupImport(importFile.value, importPassphrase.value || undefined)
    preview.value = data
  } catch (err) {
    importError.value = describe(err)
  } finally {
    previewing.value = false
  }
}

async function onImport() {
  if (!importFile.value || !preview.value) return
  const ok = await ui.confirm({
    title: t('admin_backup.confirm_title'),
    message: t('admin_backup.confirm_body', {
      shares: preview.value.shares_to_invalidate,
    }),
    confirmLabel: t('admin_backup.confirm_cta'),
    danger: true,
  })
  if (!ok) return
  importing.value = true
  importError.value = null
  try {
    const { data } = await importConfigBackup(importFile.value, importPassphrase.value || undefined)
    preview.value = data
    ui.pushToast(t('admin_backup.import_done'), 'success')
  } catch (err) {
    importError.value = describe(err)
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <div class="policy-page" data-density="operator">
    <span class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_backup.title') }}
    </span>
    <p class="fh-field-help intro">{{ t('admin_backup.intro') }}</p>

    <!-- EXPORT --------------------------------------------------------- -->
    <section class="card">
      <h2 class="sec-h2">{{ t('admin_backup.export_title') }}</h2>
      <p class="fh-field-help">{{ t('admin_backup.export_help') }}</p>

      <fieldset class="group">
        <legend>{{ t('admin_backup.categories_label') }}</legend>
        <label v-for="key in CATEGORY_KEYS" :key="key" class="toggle">
          <input v-model="selected[key]" type="checkbox" />
          <span>
            <strong>{{ t(`admin_backup.cat.${key}`) }}</strong>
            <em class="fh-field-help">{{ t(`admin_backup.cat_help.${key}`) }}</em>
          </span>
        </label>
      </fieldset>

      <fieldset class="group">
        <legend>{{ t('admin_backup.secret_label') }}</legend>
        <label v-for="mode in (['passphrase', 'ciphertext', 'exclude'] as const)" :key="mode" class="toggle">
          <input v-model="secretMode" type="radio" :value="mode" @change="onSecretModeChange" />
          <span>
            <strong>{{ t(`admin_backup.secret.${mode}`) }}</strong>
            <em class="fh-field-help">{{ t(`admin_backup.secret_help.${mode}`) }}</em>
          </span>
        </label>
      </fieldset>

      <fieldset v-if="secretMode === 'passphrase'" class="group">
        <legend>{{ t('admin_backup.passphrase_label') }}</legend>
        <input
          v-model="passphrase"
          type="password"
          class="fh-input"
          autocomplete="new-password"
          :placeholder="t('admin_backup.passphrase_ph', { n: MIN_PASSPHRASE })"
        />
        <input
          v-model="passphraseConfirm"
          type="password"
          class="fh-input"
          autocomplete="new-password"
          :placeholder="t('admin_backup.passphrase_confirm_ph')"
        />
        <p
          v-if="passphrase && !passphraseOk"
          class="fh-notice"
          data-tone="error"
        >
          {{ t('admin_backup.passphrase_mismatch', { n: MIN_PASSPHRASE }) }}
        </p>

        <label class="toggle env-toggle">
          <input v-model="includeEnv" type="checkbox" />
          <span>
            <strong>{{ t('admin_backup.include_env') }}</strong>
            <em class="fh-field-help">{{ t('admin_backup.include_env_help') }}</em>
          </span>
        </label>
      </fieldset>

      <div v-if="secretMode === 'passphrase'" class="fh-notice" data-tone="warning">
        {{ t('admin_backup.plaintext_warning') }}
      </div>

      <div class="actions">
        <button type="button" class="fh-btn" :disabled="!canExport" @click="onExport">
          {{ exporting ? t('common.loading') : t('admin_backup.export_cta') }}
        </button>
      </div>
    </section>

    <!-- IMPORT --------------------------------------------------------- -->
    <section class="card">
      <h2 class="sec-h2">{{ t('admin_backup.import_title') }}</h2>
      <div class="fh-notice" data-tone="warning">{{ t('admin_backup.import_warning') }}</div>

      <fieldset class="group">
        <legend>{{ t('admin_backup.import_file_label') }}</legend>
        <input type="file" accept=".json,application/json" @change="onPickFile" />
        <input
          v-model="importPassphrase"
          type="password"
          class="fh-input"
          autocomplete="off"
          :placeholder="t('admin_backup.import_passphrase_ph')"
        />
      </fieldset>

      <div class="actions">
        <button
          type="button"
          class="fh-btn fh-btn--ghost"
          :disabled="!importFile || previewing"
          @click="onPreview"
        >
          {{ previewing ? t('common.loading') : t('admin_backup.preview_cta') }}
        </button>
      </div>

      <div v-if="importError" class="fh-notice" data-tone="error">{{ importError }}</div>

      <!-- preview / result summary -->
      <div v-if="preview" class="summary">
        <h3 class="sum-h3">
          {{ preview.dry_run ? t('admin_backup.preview_heading') : t('admin_backup.result_heading') }}
        </h3>

        <div v-if="preview.version_warning" class="fh-notice" data-tone="warning">
          {{ preview.version_warning }}
        </div>

        <ul class="sum-list">
          <li>{{ t('admin_backup.sum_categories', { v: preview.categories.join(', ') }) }}</li>
          <li>{{ t('admin_backup.sum_secret_mode', { v: preview.secret_mode }) }}</li>
          <li class="danger">{{ t('admin_backup.sum_shares', { n: preview.shares_to_invalidate }) }}</li>
          <li v-if="preview.categories.includes('users')" class="danger">
            {{ t('admin_backup.sum_sessions') }}
          </li>
          <li v-if="preview.admins_installed?.length" class="danger">
            {{ t('admin_backup.sum_admins_installed', { n: preview.admins_installed.length }) }}:
            {{ preview.admins_installed.join(', ') }}
          </li>
          <li v-if="preview.oidc_issuers?.length" class="danger">
            {{ t('admin_backup.sum_oidc_issuers') }}: {{ preview.oidc_issuers.join(', ') }}
          </li>
          <li v-if="preview.webhook_urls?.length" class="danger">
            {{ t('admin_backup.sum_webhook_urls') }}: {{ preview.webhook_urls.join(', ') }}
          </li>
          <li v-if="preview.purged_users.length" class="danger">
            {{ t('admin_backup.sum_purge_users', { n: preview.purged_users.length }) }}:
            {{ preview.purged_users.join(', ') }}
          </li>
          <li v-if="preview.purged_groups.length" class="danger">
            {{ t('admin_backup.sum_purge_groups', { n: preview.purged_groups.length }) }}:
            {{ preview.purged_groups.join(', ') }}
          </li>
          <li v-if="!preview.dry_run">
            {{ t('admin_backup.sum_sessions_revoked', { n: preview.sessions_revoked }) }}
          </li>
        </ul>

        <div v-if="preview.warnings.length" class="fh-notice" data-tone="warning">
          <ul class="sum-list">
            <li v-for="(w, i) in preview.warnings" :key="i">{{ w }}</li>
          </ul>
        </div>

        <div v-if="preview.env_snapshot_present" class="env-block">
          <p class="fh-field-help">{{ t('admin_backup.env_present_help') }}</p>
          <pre v-if="preview.env_dotenv" class="env-pre">{{ preview.env_dotenv }}</pre>
        </div>

        <div v-if="preview.dry_run" class="actions">
          <button type="button" class="fh-btn fh-btn--danger" :disabled="importing" @click="onImport">
            {{ importing ? t('common.loading') : t('admin_backup.import_cta') }}
          </button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.intro {
  margin-bottom: 1.5rem;
  max-width: 60ch;
}
.card {
  border: var(--fh-border);
  border-radius: var(--fh-radius-md);
  padding: 1.25rem 1.5rem;
  margin-bottom: 1.5rem;
  background: var(--fh-paper-raised);
}
.sec-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.25rem;
  margin: 0 0 0.25rem;
}
.group {
  border: none;
  padding: 0;
  margin: 1.25rem 0 0;
}
.group legend {
  font-weight: 600;
  margin-bottom: 0.5rem;
  padding: 0;
}
.toggle {
  display: flex;
  gap: 0.6rem;
  align-items: flex-start;
  padding: 0.4rem 0;
}
.toggle span {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.toggle em {
  font-style: normal;
}
.env-toggle {
  margin-top: 0.75rem;
}
.fh-input {
  display: block;
  width: 100%;
  max-width: 32rem;
  margin-bottom: 0.5rem;
}
.actions {
  margin-top: 1.25rem;
}
.summary {
  margin-top: 1.5rem;
  border-top: var(--fh-border);
  padding-top: 1rem;
}
.sum-h3 {
  font-family: var(--fh-font-display);
  margin: 0 0 0.75rem;
}
.sum-list {
  margin: 0.5rem 0;
  padding-left: 1.25rem;
}
.sum-list li {
  margin: 0.2rem 0;
}
.sum-list li.danger {
  color: var(--fh-danger);
  font-weight: 600;
}
.env-block {
  margin-top: 1rem;
}
.env-pre {
  background: var(--fh-paper-sunk);
  border: var(--fh-border);
  border-radius: var(--fh-radius-md);
  padding: 0.75rem;
  overflow-x: auto;
  font-family: var(--fh-font-mono);
  font-size: 0.85rem;
  white-space: pre;
}
</style>
