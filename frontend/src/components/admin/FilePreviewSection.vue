<script setup lang="ts">
  import { ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { getFilePreviewSettings, updateFilePreviewSettings } from '@/api/admin'
  import { useSettingsSection } from '@/composables/useSettingsSection'
  import { useAuthStore } from '@/stores/auth'
  import SettingsSectionForm from '@/components/admin/SettingsSectionForm.vue'

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
  <SettingsSectionForm
    :intro="t('admin_file_preview.intro')"
    :loading="loading"
    :saving="saving"
    :error-msg="errorMsg"
    @save="onSave"
  >
    <label class="toggle-row">
      <input v-model="enabled" type="checkbox" />
      <span>
        <span class="toggle-name">{{ t('admin_file_preview.toggle_label') }}</span>
        <span class="toggle-help">{{ t('admin_file_preview.toggle_help') }}</span>
      </span>
    </label>

    <p class="fh-field-help types-note">{{ t('admin_file_preview.types_note') }}</p>
  </SettingsSectionForm>
</template>

<style scoped>
  .types-note {
    margin: 0;
    max-width: 64ch;
  }
</style>
