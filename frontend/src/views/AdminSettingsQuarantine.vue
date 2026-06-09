<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  getAvStatus,
  getQuarantineSettings,
  reloadAvSignatures,
  updateQuarantineSettings,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type { AvStatusResponse } from '@/types/api'
import { formatInSiteTime } from '@/utils/datetime'

const { t, locale } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)
const notifyAdmins = ref(false)

// v1.1.6 - AV engine status
const avStatus = ref<AvStatusResponse | null>(null)
const avLoading = ref(true)
const avReloading = ref(false)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getQuarantineSettings()
    notifyAdmins.value = data.notify_admins
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function loadAvStatus() {
  avLoading.value = true
  try {
    const { data } = await getAvStatus()
    avStatus.value = data
  } catch (err) {
    // Render the failure inline rather than toasting - the section
    // header still wants to be visible even if the status fetch failed.
    avStatus.value = {
      available: false,
      av_skip: false,
      version: null,
      sigs_version: null,
      sigs_date: null,
      raw: null,
      error: describe(err),
      last_reload_at: null,
    }
  } finally {
    avLoading.value = false
  }
}

async function onSave() {
  saving.value = true
  errorMsg.value = null
  try {
    const { data } = await updateQuarantineSettings({
      notify_admins: notifyAdmins.value,
    })
    notifyAdmins.value = data.notify_admins
    ui.pushToast(t('admin_settings_quarantine.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

async function onReloadAv() {
  avReloading.value = true
  try {
    await reloadAvSignatures()
    ui.pushToast(t('admin_av.reload_done'), 'success')
    await loadAvStatus()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    avReloading.value = false
  }
}

function fmtLastReload(iso: string | null): string {
  if (!iso) return t('admin_av.never_reloaded')
  return formatInSiteTime(iso, locale.value, { second: '2-digit' })
}

onMounted(() => {
  void load()
  void loadAvStatus()
})
</script>

<template>
  <div class="policy-page" data-density="operator">
    <span class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_settings_quarantine.title') }}
    </span>

    <p class="fh-field-help intro">{{ t('admin_settings_quarantine.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="policy-form" @submit.prevent="onSave">
      <fieldset class="toggle-fieldset">
        <label class="toggle">
          <input v-model="notifyAdmins" type="checkbox" />
          <span>{{ t('admin_settings_quarantine.toggle_label') }}</span>
        </label>
        <p class="fh-field-help">{{ t('admin_settings_quarantine.toggle_help') }}</p>
      </fieldset>

      <div v-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>

    <hr class="fh-rule av-divider" />

    <section class="av-section">
      <h2 class="av-h2">{{ t('admin_av.title') }}</h2>
      <p class="fh-field-help intro">{{ t('admin_av.intro') }}</p>

      <div v-if="avLoading" class="loading">{{ t('common.loading') }}</div>

      <template v-else-if="avStatus">
        <div
          v-if="avStatus.av_skip"
          class="fh-notice"
          data-tone="info"
        >
          {{ t('admin_av.av_skip_notice') }}
        </div>
        <div
          v-else-if="!avStatus.available"
          class="fh-notice"
          data-tone="error"
        >
          {{ t('admin_av.unavailable_notice', { error: avStatus.error ?? '-' }) }}
        </div>

        <dl class="av-kv">
          <dt>{{ t('admin_av.daemon_label') }}</dt>
          <dd class="fh-mono">{{ avStatus.version ?? '-' }}</dd>

          <dt>{{ t('admin_av.sigs_version_label') }}</dt>
          <dd class="fh-mono">{{ avStatus.sigs_version ?? '-' }}</dd>

          <dt>{{ t('admin_av.sigs_date_label') }}</dt>
          <dd class="fh-mono">{{ avStatus.sigs_date ?? '-' }}</dd>

          <dt>{{ t('admin_av.last_reload_label') }}</dt>
          <dd class="fh-mono">{{ fmtLastReload(avStatus.last_reload_at) }}</dd>
        </dl>

        <div class="actions">
          <button
            type="button"
            class="fh-btn"
            :disabled="avReloading || !avStatus.available"
            @click="onReloadAv"
          >
            {{ avReloading ? t('common.loading') : t('admin_av.reload_button') }}
          </button>
        </div>
        <p class="fh-field-help">{{ t('admin_av.reload_help') }}</p>
      </template>
    </section>
  </div>
</template>

<style scoped>
.policy-page {
  max-width: none;
}

.intro {
  margin: var(--fh-space-2) 0 var(--fh-space-3);
  max-width: 64ch;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-4) 0;
}

.policy-form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-4);
  margin-top: var(--fh-space-3);
}

.toggle-fieldset {
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-2);
  cursor: pointer;
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
}

.av-divider {
  margin: var(--fh-space-6) 0 var(--fh-space-4);
}

.av-section {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-3);
}

.av-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  letter-spacing: -0.01em;
  margin: 0;
  color: var(--fh-ink);
}

.av-kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  column-gap: var(--fh-space-4);
  row-gap: var(--fh-space-2);
  margin: 0;
}

.av-kv dt {
  color: var(--fh-subtle);
  font-size: var(--fh-text-body-sm);
}

.av-kv dd {
  margin: 0;
}
</style>
