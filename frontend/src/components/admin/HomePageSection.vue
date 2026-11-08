<script setup lang="ts">
  import { ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { getHomePageSettings, updateHomePageSettings } from '@/api/admin'
  import { useSettingsSection } from '@/composables/useSettingsSection'
  import { useAuthStore } from '@/stores/auth'
  import SettingsSectionForm from '@/components/admin/SettingsSectionForm.vue'

  const { t } = useI18n()
  const auth = useAuthStore()

  const enabled = ref(true)
  const { loading, saving, errorMsg, onSave } = useSettingsSection({
    load: getHomePageSettings,
    save: () => updateHomePageSettings({ enabled: enabled.value }),
    apply: (data) => {
      enabled.value = data.enabled
    },
    // Refresh /me so AppHeader's brand-mark + the user's landing picker
    // reflect the change without a manual reload.
    afterSave: () => auth.refreshMe(),
    savedToastKey: 'admin_home_page.saved_toast',
  })
</script>

<template>
  <SettingsSectionForm
    :intro="t('admin_home_page.intro')"
    :loading="loading"
    :saving="saving"
    :error-msg="errorMsg"
    @save="onSave"
  >
    <label class="toggle-row">
      <input v-model="enabled" type="checkbox" />
      <span>
        <span class="toggle-name">{{ t('admin_home_page.toggle_label') }}</span>
        <span class="toggle-help">{{ t('admin_home_page.toggle_help') }}</span>
      </span>
    </label>

    <ul class="effects">
      <li>{{ t('admin_home_page.effect_picker') }}</li>
      <li>{{ t('admin_home_page.effect_brand') }}</li>
      <li>{{ t('admin_home_page.effect_redirect') }}</li>
    </ul>
  </SettingsSectionForm>
</template>

<style scoped>
  .effects {
    list-style: disc;
    padding-left: var(--fh-space-4);
    margin: 0;
    color: var(--fh-subtle);
    font-size: var(--fh-text-body-sm);
  }

  .effects li {
    margin-bottom: var(--fh-space-1);
  }
</style>
