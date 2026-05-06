<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getShareDefaults, updateShareDefaults } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()
const auth = useAuthStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)
const notifyDefault = ref(true)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getShareDefaults()
    notifyDefault.value = data.notify_recipients_default
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
    const { data } = await updateShareDefaults({
      notify_recipients_default: notifyDefault.value,
    })
    notifyDefault.value = data.notify_recipients_default
    // Refresh /me so the share-create form picks up the new default
    // without a manual reload.
    await auth.refreshMe()
    ui.pushToast(t('admin_share_defaults.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="share-defaults-section">
    <p class="fh-field-help intro">{{ t('admin_share_defaults.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="form" @submit.prevent="onSave">
      <label class="toggle-row">
        <input v-model="notifyDefault" type="checkbox" />
        <span>
          <span class="toggle-name">{{ t('admin_share_defaults.toggle_label') }}</span>
          <span class="toggle-help">{{ t('admin_share_defaults.toggle_help') }}</span>
        </span>
      </label>

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
.share-defaults-section {
  max-width: 640px;
}

.intro {
  margin: 0 0 var(--fh-space-3);
  max-width: 64ch;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-4) 0;
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-3);
}

.toggle-row {
  display: flex;
  gap: var(--fh-space-2);
  align-items: flex-start;
  cursor: pointer;
  padding: var(--fh-space-3);
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
}

.toggle-row > span {
  display: flex;
  flex-direction: column;
}

.toggle-name {
  font-weight: 500;
}

.toggle-help {
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
}
</style>
