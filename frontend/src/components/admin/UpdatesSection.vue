<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getUpdatesSettings, updateUpdatesSettings } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)
const apiUrl = ref('')

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getUpdatesSettings()
    apiUrl.value = data.api_url
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
    const { data } = await updateUpdatesSettings({ api_url: apiUrl.value.trim() })
    apiUrl.value = data.api_url
    ui.pushToast(t('admin_updates.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="updates-section">
    <p class="fh-field-help intro">{{ t('admin_updates.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="form" @submit.prevent="onSave">
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_updates.url_label') }}</span>
        <input
          v-model.trim="apiUrl"
          class="fh-field-input fh-field-mono"
          type="url"
          required
          :placeholder="t('admin_updates.url_placeholder')"
        />
        <span class="fh-field-help">{{ t('admin_updates.url_help') }}</span>
      </label>

      <p class="fh-field-help">
        {{ t('admin_updates.schedule_moved') }}
        <RouterLink :to="{ name: 'admin-scheduled-tasks' }">{{ t('admin.nav.scheduled_tasks') }}</RouterLink>
      </p>

      <div
v-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving || !apiUrl">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.updates-section { max-width: 640px; }
.intro { margin: 0 0 var(--fh-space-3); max-width: 64ch; }
.loading { color: var(--fh-subtle); padding: var(--fh-space-4) 0; }
.form { display: flex; flex-direction: column; gap: var(--fh-space-3); }
.actions { display: flex; gap: var(--fh-space-3); }
</style>
