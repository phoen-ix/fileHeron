<script setup lang="ts">
  import { ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { getUpdatesSettings, updateUpdatesSettings } from '@/api/admin'
  import { useSettingsSection } from '@/composables/useSettingsSection'

  const { t } = useI18n()

  const apiUrl = ref('')
  const { loading, saving, errorMsg, onSave } = useSettingsSection({
    load: getUpdatesSettings,
    save: () => updateUpdatesSettings({ api_url: apiUrl.value.trim() }),
    apply: (data) => {
      apiUrl.value = data.api_url
    },
    savedToastKey: 'admin_updates.saved_toast',
  })
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
        <RouterLink :to="{ name: 'admin-scheduled-tasks' }">{{
          t('admin.nav.scheduled_tasks')
        }}</RouterLink>
      </p>

      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving || !apiUrl">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
  .updates-section {
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
  .actions {
    display: flex;
    gap: var(--fh-space-3);
  }
</style>
