<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  getMaintenanceSettings,
  updateMaintenanceSettings,
  type MaintenanceSettingsResponse,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useSiteStore } from '@/stores/site'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()
const site = useSiteStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)
const enabled = ref(false)
const message = ref('')
const activity = ref<{ uploads: number; downloads: number }>({ uploads: 0, downloads: 0 })

function apply(data: MaintenanceSettingsResponse) {
  enabled.value = data.enabled
  message.value = data.message
  activity.value = { uploads: data.active_uploads, downloads: data.active_downloads }
}

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getMaintenanceSettings()
    apply(data)
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
    const { data } = await updateMaintenanceSettings({
      enabled: enabled.value,
      message: message.value,
    })
    apply(data)
    // Refresh the global banner immediately (config-public drives it).
    await site.loadConfig()
    ui.pushToast(t('admin_maintenance.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <div class="policy-page" data-density="operator">
    <span class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_maintenance.title') }}
    </span>
    <p class="fh-field-help intro">{{ t('admin_maintenance.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="policy-form" @submit.prevent="onSave">
      <fieldset class="toggle-fieldset">
        <label class="toggle">
          <input v-model="enabled" type="checkbox" />
          <span>{{ t('admin_maintenance.toggle_label') }}</span>
        </label>
        <p class="fh-field-help">{{ t('admin_maintenance.toggle_help') }}</p>
      </fieldset>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_maintenance.message_label') }}</span>
        <input
          v-model="message"
          type="text"
          class="fh-field-input"
          maxlength="280"
          :placeholder="t('admin_maintenance.message_ph')"
        />
        <span class="fh-field-help">{{ t('admin_maintenance.message_help') }}</span>
      </label>

      <p class="fh-field-help activity">
        {{ t('admin_maintenance.activity', {
          up: activity.uploads,
          down: activity.downloads,
        }) }}
      </p>

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
.intro {
  margin-bottom: 1.5rem;
  max-width: 60ch;
}
.toggle {
  display: flex;
  gap: 0.6rem;
  align-items: center;
}
.toggle-fieldset {
  border: none;
  padding: 0;
  margin: 0 0 1.25rem;
}
.fh-field {
  display: block;
  max-width: none;
  margin-bottom: 1rem;
}
.activity {
  margin: 0.5rem 0 1rem;
}
.actions {
  margin-top: 1rem;
}
</style>
