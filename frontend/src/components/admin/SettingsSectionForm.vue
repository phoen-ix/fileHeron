<script setup lang="ts">
  /* The chrome every small `useSettingsSection` page section repeated: intro,
   * loading line, form, error notice, Save button - and the toggle-row look
   * the sections share (audit 2026-11-08). The fields are the slot; styles for
   * the shared field classes reach them through `:slotted`. */
  import { useI18n } from 'vue-i18n'

  withDefaults(
    defineProps<{
      intro: string
      loading: boolean
      saving: boolean
      errorMsg: string | null
      /** Extra condition for the Save button (e.g. a required field). */
      canSave?: boolean
    }>(),
    { canSave: true },
  )
  const emit = defineEmits<{ save: [] }>()
  const { t } = useI18n()
</script>

<template>
  <div class="settings-section">
    <p class="fh-field-help intro">{{ intro }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="form" @submit.prevent="emit('save')">
      <slot />

      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving || !canSave">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
  .settings-section {
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

  .form :slotted(.toggle-row) {
    display: flex;
    gap: var(--fh-space-2);
    align-items: flex-start;
    cursor: pointer;
    padding: var(--fh-space-3);
    border: 1px solid var(--fh-rule);
    border-radius: var(--fh-radius-sm);
  }

  .form :slotted(.toggle-row > span) {
    display: flex;
    flex-direction: column;
  }

  .form :slotted(.toggle-name) {
    font-weight: 500;
  }

  .form :slotted(.toggle-help) {
    font-size: var(--fh-text-body-sm);
    color: var(--fh-subtle);
  }

  .actions {
    display: flex;
    gap: var(--fh-space-3);
  }
</style>
