<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import {
    getAutoUpdateSettings,
    updateAutoUpdateSettings,
    type AutoUpdateScope,
    type AutoUpdateSettingsResponse,
    type UpdateAutoUpdateSettingsRequest,
  } from '@/api/admin'
  import { useApiError } from '@/composables/useApiError'
  import { useUiStore } from '@/stores/ui'

  const emit = defineEmits<{ saved: [settings: AutoUpdateSettingsResponse] }>()

  const { t } = useI18n()
  const { describe } = useApiError()
  const ui = useUiStore()

  const SCOPES: AutoUpdateScope[] = ['patch', 'minor', 'any']

  const loading = ref(true)
  const saving = ref(false)
  const errorMsg = ref<string | null>(null)
  const saved = ref<AutoUpdateSettingsResponse | null>(null)
  const enabled = ref(false)
  const scope = ref<AutoUpdateScope>('patch')
  const minAgeHours = ref(24)
  const password = ref('')

  const changed = computed(
    () =>
      saved.value !== null &&
      (enabled.value !== saved.value.enabled ||
        scope.value !== saved.value.scope ||
        minAgeHours.value !== saved.value.min_age_hours),
  )
  // Mirrors the backend rule: an automatic update skips the password every
  // manual one asks for, so turning it on - or changing it while it is on -
  // needs the password. Turning it off does not.
  const needsPassword = computed(() => enabled.value && changed.value)

  function apply(data: AutoUpdateSettingsResponse) {
    saved.value = data
    enabled.value = data.enabled
    scope.value = data.scope
    minAgeHours.value = data.min_age_hours
  }

  async function load() {
    loading.value = true
    errorMsg.value = null
    try {
      const { data } = await getAutoUpdateSettings()
      apply(data)
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      loading.value = false
    }
  }

  async function onSave() {
    if (!saved.value || !changed.value) return
    saving.value = true
    errorMsg.value = null
    const payload: UpdateAutoUpdateSettingsRequest = {
      enabled: enabled.value,
      scope: scope.value,
      min_age_hours: minAgeHours.value,
    }
    if (needsPassword.value) payload.password = password.value
    try {
      const { data } = await updateAutoUpdateSettings(payload)
      apply(data)
      password.value = ''
      ui.pushToast(t('admin_auto_update.saved_toast'), 'success')
      emit('saved', data)
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      saving.value = false
    }
  }

  onMounted(load)
</script>

<template>
  <section id="auto-update" class="auto-update">
    <h3 class="section-h">{{ t('admin_auto_update.title') }}</h3>
    <p class="fh-field-help intro">{{ t('admin_auto_update.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="form" @submit.prevent="onSave">
      <div>
        <label class="toggle">
          <input v-model="enabled" type="checkbox" data-testid="auto-update-enabled" />
          <span>{{ t('admin_auto_update.enabled_label') }}</span>
        </label>
      </div>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_auto_update.scope_label') }}</span>
        <select v-model="scope" class="fh-field-input" data-testid="auto-update-scope">
          <option v-for="s in SCOPES" :key="s" :value="s">
            {{ t(`admin_auto_update.scope.${s}`) }}
          </option>
        </select>
        <span class="fh-field-help">{{ t('admin_auto_update.scope_help') }}</span>
      </label>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_auto_update.min_age_label') }}</span>
        <input
          v-model.number="minAgeHours"
          class="fh-field-input hours"
          type="number"
          min="0"
          max="720"
          step="1"
          required
          data-testid="auto-update-min-age"
        />
        <span class="fh-field-help">{{ t('admin_auto_update.min_age_help') }}</span>
      </label>

      <p class="fh-field-help">
        {{ t('admin_auto_update.schedule') }}
        <RouterLink :to="{ name: 'admin-scheduled-tasks' }">{{
          t('admin.nav.scheduled_tasks')
        }}</RouterLink>
      </p>

      <div
        v-if="saved?.skipped_tag"
        class="fh-notice"
        data-tone="accent"
        data-testid="auto-update-skipped"
      >
        {{ t('admin_auto_update.skipped', { tag: saved.skipped_tag }) }}
      </div>

      <label v-if="needsPassword" class="fh-field">
        <span class="fh-field-label">{{ t('admin_auto_update.password_label') }}</span>
        <input
          v-model="password"
          class="fh-field-input"
          type="password"
          autocomplete="current-password"
          required
          data-testid="auto-update-password"
        />
        <span class="fh-field-help">{{ t('admin_auto_update.password_help') }}</span>
      </label>

      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button
          type="submit"
          class="fh-btn"
          :disabled="saving || !changed || (needsPassword && !password)"
          data-testid="auto-update-save"
        >
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>
  </section>
</template>

<style scoped>
  .auto-update {
    max-width: 640px;
    margin-top: var(--fh-space-5);
  }
  .section-h {
    margin: 0 0 var(--fh-space-2);
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
  .toggle {
    display: inline-flex;
    align-items: center;
    gap: var(--fh-space-2);
    cursor: pointer;
  }
  .hours {
    max-width: 10ch;
  }
  .actions {
    display: flex;
    gap: var(--fh-space-3);
  }
</style>
