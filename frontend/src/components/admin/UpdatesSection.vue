<script setup lang="ts">
  import { ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { getUpdatesSettings, updateUpdatesSettings } from '@/api/admin'
  import { useSettingsSection } from '@/composables/useSettingsSection'
  import SettingsSectionForm from '@/components/admin/SettingsSectionForm.vue'

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
  <SettingsSectionForm
    :intro="t('admin_updates.intro')"
    :loading="loading"
    :saving="saving"
    :error-msg="errorMsg"
    :can-save="!!apiUrl"
    @save="onSave"
  >
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
  </SettingsSectionForm>
</template>
