<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getSiteSettings, updateSiteSettings } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useSiteStore } from '@/stores/site'
import { useUiStore } from '@/stores/ui'
import type { SiteSettingsResponse } from '@/types/api'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()
const site = useSiteStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)
const data = ref<SiteSettingsResponse | null>(null)
const draft = ref('')

/* IANA zone catalogue for the <datalist> typeahead. supportedValuesOf
 * is well-supported since 2022 (Chrome 99, FF 93, Safari 15.4). Falls
 * back to a short curated list if the browser is too old to expose it
 * - the admin can still type any value, server-side validation is the
 * gate that matters. */
const ALL_ZONES: readonly string[] = (() => {
  const sv = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf
  if (typeof sv === 'function') return sv('timeZone')
  return ['UTC', 'Europe/Vienna', 'Europe/Berlin', 'Europe/London', 'America/New_York', 'America/Los_Angeles', 'Asia/Tokyo']
})()

const browserTz = computed<string>(() => {
  try {
    return new Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
})

const draftIsValid = computed<boolean>(() => {
  const v = draft.value.trim()
  if (!v) return true // empty = clear back to default
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: v })
    return true
  } catch {
    return false
  }
})

const dirty = computed(
  () => data.value !== null && draft.value.trim() !== data.value.site_timezone,
)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data: resp } = await getSiteSettings()
    data.value = resp
    draft.value = resp.site_timezone
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onSave() {
  if (!draftIsValid.value) {
    errorMsg.value = t('admin_site_timezone.invalid_tz', { tz: draft.value.trim() })
    return
  }
  saving.value = true
  errorMsg.value = null
  try {
    const { data: resp } = await updateSiteSettings({
      site_timezone: draft.value.trim(),
    })
    data.value = resp
    draft.value = resp.site_timezone
    // Refresh the site store so the new tz takes effect on every other
    // open admin tab / mounted view without a hard reload.
    await site.loadConfig()
    ui.pushToast(t('admin_site_timezone.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

async function useBrowserTz() {
  draft.value = browserTz.value
}

onMounted(load)
</script>

<template>
  <div class="site-tz-section">
    <p class="fh-field-help intro">{{ t('admin_site_timezone.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else-if="data" @submit.prevent="onSave">
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_site_timezone.timezone_label') }}</span>
        <input
          v-model="draft"
          list="iana-timezones"
          type="text"
          class="fh-field-input fh-field-mono"
          placeholder="UTC"
          spellcheck="false"
          autocomplete="off"
        />
        <span class="fh-field-help">{{ t('admin_site_timezone.timezone_help') }}</span>
      </label>

      <datalist id="iana-timezones">
        <option v-for="z in ALL_ZONES" :key="z" :value="z" />
      </datalist>

      <p class="fh-field-help meta-line">
        {{ t('admin_site_timezone.browser_hint', { tz: browserTz }) }}
        <button
          type="button"
          class="fh-btn-text use-browser"
          :disabled="saving || draft.trim() === browserTz"
          @click="useBrowserTz"
        >
          {{ t('admin_site_timezone.use_browser') }}
        </button>
      </p>

      <div
v-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button
          type="submit"
          class="fh-btn"
          :disabled="saving || !dirty || !draftIsValid"
        >
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.site-tz-section {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-3);
}

.intro {
  margin: 0;
  max-width: 64ch;
}

.meta-line {
  display: flex;
  align-items: baseline;
  gap: var(--fh-space-2);
  margin: 0;
}

.use-browser {
  font-size: var(--fh-text-body-sm);
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
