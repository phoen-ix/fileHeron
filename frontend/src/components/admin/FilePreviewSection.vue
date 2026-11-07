<script setup lang="ts">
  import { ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { getFilePreviewSettings, updateFilePreviewSettings } from '@/api/admin'
  import { useSettingsSection } from '@/composables/useSettingsSection'
  import { useAuthStore } from '@/stores/auth'

  const { t } = useI18n()
  const auth = useAuthStore()

  const enabled = ref(true)
  const { loading, saving, errorMsg, onSave } = useSettingsSection({
    load: getFilePreviewSettings,
    save: () => updateFilePreviewSettings({ enabled: enabled.value }),
    apply: (data) => {
      enabled.value = data.enabled
    },
    // Refresh /me so the Preview buttons in share views reflect the change
    // without a manual reload.
    afterSave: () => auth.refreshMe(),
    savedToastKey: 'admin_file_preview.saved_toast',
  })
</script>

<template>
  <div class="file-preview-section">
    <p class="fh-field-help intro">{{ t('admin_file_preview.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="form" @submit.prevent="onSave">
      <label class="toggle-row">
        <input v-model="enabled" type="checkbox" />
        <span>
          <span class="toggle-name">{{ t('admin_file_preview.toggle_label') }}</span>
          <span class="toggle-help">{{ t('admin_file_preview.toggle_help') }}</span>
        </span>
      </label>

      <p class="fh-field-help types-note">{{ t('admin_file_preview.types_note') }}</p>

      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
  .file-preview-section {
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

  .types-note {
    margin: 0;
    max-width: 64ch;
  }

  .actions {
    display: flex;
    gap: var(--fh-space-3);
  }
</style>
