<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  fetchInboxNow,
  getImapSettings,
  testImap,
  updateImapSettings,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type { ImapSettingsResponse, ImapTestResponse } from '@/types/api'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const fetching = ref(false)
const errorMsg = ref<string | null>(null)
const testResult = ref<ImapTestResponse | null>(null)
const isPasswordSet = ref(false)
const lastPollAt = ref<string | null>(null)
const passwordTouched = ref(false)

const form = ref({
  enabled: false,
  check_mode: 'auto' as 'auto' | 'manual',
  host: '',
  port: 993,
  user: '',
  password: '',
  tls_mode: 'implicit' as 'implicit' | 'starttls' | 'none',
  mailbox: 'INBOX',
  post_fetch_action: 'mark_read' as 'mark_read' | 'untouched' | 'move' | 'delete',
  move_folder: 'fileHeron/Processed',
  notify_mode: 'off' as 'off' | 'human' | 'all',
  poll_interval_minutes: 5,
})

function hydrate(s: ImapSettingsResponse) {
  form.value = {
    enabled: s.enabled,
    check_mode: s.check_mode,
    host: s.host,
    port: s.port,
    user: s.user,
    password: '',
    tls_mode: s.tls_mode,
    mailbox: s.mailbox,
    post_fetch_action: s.post_fetch_action,
    move_folder: s.move_folder,
    notify_mode: s.notify_mode,
    poll_interval_minutes: s.poll_interval_minutes,
  }
  isPasswordSet.value = s.is_password_set
  lastPollAt.value = s.last_poll_at
  passwordTouched.value = false
}

async function load() {
  loading.value = true
  try {
    const { data } = await getImapSettings()
    hydrate(data)
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
    const { data } = await updateImapSettings({
      ...form.value,
      password: passwordTouched.value ? form.value.password : null,
    })
    hydrate(data)
    ui.pushToast(t('admin_imap.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

async function onTest() {
  testing.value = true
  testResult.value = null
  try {
    const { data } = await testImap()
    testResult.value = data
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    testing.value = false
  }
}

async function onFetchNow() {
  fetching.value = true
  try {
    const { data } = await fetchInboxNow()
    if (data.ok && data.skipped) {
      ui.pushToast(t('admin_imap.fetch_skipped', { reason: data.skipped }), 'warn')
    } else if (data.ok) {
      ui.pushToast(t('admin_imap.fetch_done', { n: data.ingested ?? 0 }), 'success')
    } else {
      ui.pushToast(data.error || t('admin_imap.fetch_failed'), 'warn')
    }
  } catch (err) {
    ui.pushToast(describe(err), 'warn')
  } finally {
    fetching.value = false
  }
}

const actionOptions = ['mark_read', 'untouched', 'move', 'delete'] as const
const notifyOptions = ['off', 'human', 'all'] as const

onMounted(load)
</script>

<template>
  <div class="policy-page" data-density="operator">
    <span class="fh-eyebrow">{{ t('admin_settings.eyebrow') }} / {{ t('admin_imap.title') }}</span>
    <p class="fh-field-help intro">{{ t('admin_imap.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="policy-form" @submit.prevent="onSave">
      <label class="toggle-row">
        <input v-model="form.enabled" type="checkbox" />
        <span>
          <span class="mode-name">{{ t('admin_imap.enable_label') }}</span>
          <span class="mode-help">{{ t('admin_imap.enable_help') }}</span>
        </span>
      </label>

      <div v-if="errorMsg" class="fh-notice" data-tone="danger">{{ errorMsg }}</div>

      <h2 class="form-h2">{{ t('admin_imap.connection') }}</h2>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_imap.host') }}</span>
        <input v-model.trim="form.host" type="text" class="fh-field-input" autocomplete="off" />
      </label>
      <div class="row-2">
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_imap.port') }}</span>
          <input v-model.number="form.port" type="number" class="fh-field-input" min="1" max="65535" />
        </label>
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_imap.tls') }}</span>
          <select v-model="form.tls_mode" class="fh-field-input">
            <option value="implicit">{{ t('admin_imap.tls_implicit') }}</option>
            <option value="starttls">{{ t('admin_imap.tls_starttls') }}</option>
            <option value="none">{{ t('admin_imap.tls_none') }}</option>
          </select>
        </label>
      </div>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_imap.user') }}</span>
        <input v-model.trim="form.user" type="text" class="fh-field-input" autocomplete="off" />
      </label>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_imap.password') }}</span>
        <input
          v-model="form.password"
          type="password"
          class="fh-field-input"
          autocomplete="new-password"
          :placeholder="isPasswordSet ? '••••••••' : ''"
          @input="passwordTouched = true"
        />
        <span class="fh-field-help">
          {{ isPasswordSet ? t('admin_imap.password_set_help') : t('admin_imap.password_help') }}
        </span>
      </label>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_imap.mailbox') }}</span>
        <input v-model.trim="form.mailbox" type="text" class="fh-field-input" />
      </label>

      <h2 class="form-h2">{{ t('admin_imap.behaviour') }}</h2>
      <div class="row-2">
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_imap.check_mode') }}</span>
          <select v-model="form.check_mode" class="fh-field-input">
            <option value="auto">{{ t('admin_imap.mode_auto') }}</option>
            <option value="manual">{{ t('admin_imap.mode_manual') }}</option>
          </select>
        </label>
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_imap.interval') }}</span>
          <input v-model.number="form.poll_interval_minutes" type="number" class="fh-field-input" min="1" max="1440" />
        </label>
      </div>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_imap.post_fetch') }}</span>
        <select v-model="form.post_fetch_action" class="fh-field-input">
          <option v-for="a in actionOptions" :key="a" :value="a">{{ t(`admin_imap.action_${a}`) }}</option>
        </select>
        <span class="fh-field-help">{{ t(`admin_imap.action_${form.post_fetch_action}_help`) }}</span>
      </label>
      <label v-if="form.post_fetch_action === 'move'" class="fh-field">
        <span class="fh-field-label">{{ t('admin_imap.move_folder') }}</span>
        <input v-model.trim="form.move_folder" type="text" class="fh-field-input" />
      </label>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_imap.notify') }}</span>
        <select v-model="form.notify_mode" class="fh-field-input">
          <option v-for="n in notifyOptions" :key="n" :value="n">{{ t(`admin_imap.notify_${n}`) }}</option>
        </select>
      </label>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving">{{ t('common.save') }}</button>
        <button type="button" class="fh-btn-text" :disabled="testing" @click="onTest">
          {{ t('admin_imap.test') }}
        </button>
        <button type="button" class="fh-btn-text" :disabled="fetching" @click="onFetchNow">
          {{ t('admin_imap.fetch_now') }}
        </button>
      </div>

      <div v-if="testResult" class="fh-notice" :data-tone="testResult.ok ? 'success' : 'danger'">
        <strong>{{ testResult.ok ? t('admin_imap.test_ok') : t('admin_imap.test_fail') }}</strong>
        <template v-if="testResult.ok">
          <span> — {{ t('admin_imap.folders') }}: {{ testResult.folders.join(', ') }}</span>
        </template>
        <template v-else>
          <div class="fh-mono">{{ testResult.error }}</div>
          <div v-if="testResult.hint">{{ testResult.hint }}</div>
        </template>
      </div>
    </form>
  </div>
</template>

<style scoped>
.intro {
  margin: var(--fh-space-2) 0 var(--fh-space-4);
}
.row-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--fh-space-3);
}
.toggle-row {
  display: flex;
  gap: var(--fh-space-2);
  align-items: flex-start;
  margin-bottom: var(--fh-space-4);
}
.mode-name {
  display: block;
  font-weight: 500;
}
.mode-help {
  display: block;
  color: var(--fh-ink-soft);
  font-size: var(--fh-text-body-sm);
}
.fh-field {
  display: block;
  margin-bottom: var(--fh-space-3);
}
.actions {
  display: flex;
  gap: var(--fh-space-3);
  align-items: center;
  margin: var(--fh-space-4) 0;
}
</style>
