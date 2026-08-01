<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getSiteSettings, updateSiteSettings } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type { SiteSettingsResponse } from '@/types/api'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)
const data = ref<SiteSettingsResponse | null>(null)
const draft = ref('')

const dirty = computed(
  () => data.value !== null && draft.value.trim() !== data.value.site_url,
)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data: resp } = await getSiteSettings()
    data.value = resp
    draft.value = resp.site_url
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
    const { data: resp } = await updateSiteSettings({
      site_url: draft.value.trim() || null,
    })
    data.value = resp
    draft.value = resp.site_url
    ui.pushToast(t('admin_site_url.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

async function onRevert() {
  saving.value = true
  errorMsg.value = null
  try {
    const { data: resp } = await updateSiteSettings({ site_url: null })
    data.value = resp
    draft.value = resp.site_url
    ui.pushToast(t('admin_site_url.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="site-url-section">
    <p class="fh-field-help intro">{{ t('admin_site_url.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else-if="data" @submit.prevent="onSave">
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_site_url.url_label') }}</span>
        <input
          v-model="draft"
          type="url"
          class="fh-field-input fh-field-mono"
          placeholder="https://files.example.com"
          spellcheck="false"
          autocomplete="off"
        />
        <span class="fh-field-help">{{ t('admin_site_url.url_help') }}</span>
      </label>

      <p class="fh-field-help env-line">
        {{ t('admin_site_url.env_fallback', { url: data.env_app_url }) }}
        <span v-if="data.has_db_override" class="fh-pill" data-state="active">
          {{ t('admin_site_url.override_active') }}
        </span>
      </p>

      <p class="fh-field-help security-note">
        {{ t('admin_site_url.security_note') }}
      </p>

      <div
v-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button
          type="submit"
          class="fh-btn"
          :disabled="saving || !dirty"
        >
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
        <button
          v-if="data.has_db_override"
          type="button"
          class="fh-btn-text"
          :disabled="saving"
          @click="onRevert"
        >
          {{ t('admin_site_url.revert_to_env') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.site-url-section {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-3);
}

.intro {
  margin: 0;
  max-width: 64ch;
}

.env-line {
  display: flex;
  align-items: baseline;
  gap: var(--fh-space-2);
  margin: 0;
}

.security-note {
  margin: 0;
  font-style: italic;
  max-width: 64ch;
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-1);
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-3) 0;
}
</style>
