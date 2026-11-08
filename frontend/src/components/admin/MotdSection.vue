<script setup lang="ts">
  import { ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { getMotdSettings, updateMotdSettings } from '@/api/admin'
  import { useSettingsSection } from '@/composables/useSettingsSection'
  import SettingsSectionForm from '@/components/admin/SettingsSectionForm.vue'

  const { t } = useI18n()

  const enabled = ref(false)
  const text = ref('')

  const MAX_LEN = 500

  const { loading, saving, errorMsg, onSave } = useSettingsSection({
    load: getMotdSettings,
    save: () => updateMotdSettings({ enabled: enabled.value, text: text.value }),
    apply: (data) => {
      enabled.value = data.enabled
      text.value = data.text
    },
    savedToastKey: 'admin_motd.saved_toast',
  })
</script>

<template>
  <SettingsSectionForm
    :intro="t('admin_motd.intro')"
    :loading="loading"
    :saving="saving"
    :error-msg="errorMsg"
    @save="onSave"
  >
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
  </SettingsSectionForm>
</template>

<style scoped>
  .motd-textarea {
    resize: vertical;
    min-height: 4em;
    font-family: var(--fh-font-body);
  }
</style>
