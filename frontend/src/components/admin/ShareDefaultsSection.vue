<script setup lang="ts">
  import { ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { getShareDefaults, updateShareDefaults } from '@/api/admin'
  import { useSettingsSection } from '@/composables/useSettingsSection'
  import { useAuthStore } from '@/stores/auth'
  import SettingsSectionForm from '@/components/admin/SettingsSectionForm.vue'

  const { t } = useI18n()
  const auth = useAuthStore()

  const notifyDefault = ref(true)
  const { loading, saving, errorMsg, onSave } = useSettingsSection({
    load: getShareDefaults,
    save: () => updateShareDefaults({ notify_recipients_default: notifyDefault.value }),
    apply: (data) => {
      notifyDefault.value = data.notify_recipients_default
    },
    // Refresh /me so the share-create form picks up the new default without a
    // manual reload.
    afterSave: () => auth.refreshMe(),
    savedToastKey: 'admin_share_defaults.saved_toast',
  })
</script>

<template>
  <SettingsSectionForm
    :intro="t('admin_share_defaults.intro')"
    :loading="loading"
    :saving="saving"
    :error-msg="errorMsg"
    @save="onSave"
  >
    <label class="toggle-row">
      <input v-model="notifyDefault" type="checkbox" />
      <span>
        <span class="toggle-name">{{ t('admin_share_defaults.toggle_label') }}</span>
        <span class="toggle-help">{{ t('admin_share_defaults.toggle_help') }}</span>
      </span>
    </label>
  </SettingsSectionForm>
</template>
