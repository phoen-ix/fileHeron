<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getMotdSettings, updateMotdSettings } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)
const enabled = ref(false)
const text = ref('')

const MAX_LEN = 500

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getMotdSettings()
    enabled.value = data.enabled
    text.value = data.text
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
    const { data } = await updateMotdSettings({
      enabled: enabled.value,
      text: text.value,
    })
    enabled.value = data.enabled
    text.value = data.text
    ui.pushToast(t('admin_motd.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="motd-section">
    <p class="fh-field-help intro">{{ t('admin_motd.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="form" @submit.prevent="onSave">
      <label class="toggle-row">
        <input v-model="enabled" type="checkbox" />
        <span>
          <span class="toggle-name">{{ t('admin_motd.toggle_label') }}</span>
          <span class="toggle-help">{{ t('admin_motd.toggle_help') }}</span>
        </span>
      </label>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_motd.text_label') }}</span>
        <textarea
          v-model="text"
          class="fh-field-input motd-textarea"
          :maxlength="MAX_LEN"
          :placeholder="t('admin_motd.text_placeholder')"
          rows="3"
        ></textarea>
        <span class="fh-field-help">
          {{ t('admin_motd.text_help', { remaining: MAX_LEN - text.length }) }}
        </span>
      </label>

      <div
v-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.motd-section { max-width: 640px; }
.intro { margin: 0 0 var(--fh-space-3); max-width: 64ch; }
.loading { color: var(--fh-subtle); padding: var(--fh-space-4) 0; }
.form { display: flex; flex-direction: column; gap: var(--fh-space-3); }
.toggle-row {
  display: flex;
  gap: var(--fh-space-2);
  align-items: flex-start;
  cursor: pointer;
  padding: var(--fh-space-3);
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
}
.toggle-row > span { display: flex; flex-direction: column; }
.toggle-name { font-weight: 500; }
.toggle-help { font-size: var(--fh-text-body-sm); color: var(--fh-subtle); }
.motd-textarea { resize: vertical; min-height: 4em; font-family: var(--fh-font-body); }
.actions { display: flex; gap: var(--fh-space-3); }
</style>
