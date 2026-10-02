<script setup lang="ts">
  /* Encryption at rest of stored files. Turning it on commits file recovery to
   * this instance's .env, so the page makes the admin say they know before the
   * password prompt opens - and the backend refuses without that
   * acknowledgement too (ENCRYPTION_ACK_REQUIRED). Both directions are step-up
   * gated. */
  import { computed, onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import {
    getEncryptionSettings,
    retryFailedEncryption,
    updateEncryptionSettings,
    type EncryptionSettingsResponse,
  } from '@/api/admin'
  import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
  import StepUpDialog from '@/components/StepUpDialog.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useUiStore } from '@/stores/ui'
  import { formatBytes } from '@/utils/bytes'
  import { formatInSiteTime } from '@/utils/datetime'

  const { t, locale } = useI18n()
  const { describe } = useApiError()
  const ui = useUiStore()

  const loading = ref(true)
  const errorMsg = ref<string | null>(null)
  const status = ref<EncryptionSettingsResponse | null>(null)
  const acknowledged = ref(false)
  const saving = ref(false)
  const stepUpOpen = ref(false)
  const stepUpError = ref<string | null>(null)
  const retrying = ref(false)

  // What the open dialog will do - fixed when it opens, so a status reload
  // underneath it cannot flip the direction mid-prompt.
  const target = ref(false)

  const backendLabel = computed(() => {
    const b = status.value?.backend
    if (b === 'local') return t('admin_encryption.backend.local')
    if (b === 's3') return t('admin_encryption.backend.s3')
    return b ?? '-'
  })

  async function load() {
    loading.value = true
    errorMsg.value = null
    try {
      const { data } = await getEncryptionSettings()
      status.value = data
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      loading.value = false
    }
  }

  const stoppedLabel = computed(() => {
    const stopped = status.value?.last_run?.stopped
    if (stopped === 'budget') return t('admin_encryption.stopped_budget')
    if (stopped === 'insufficient_space') return t('admin_encryption.stopped_insufficient_space')
    if (stopped === 'disabled') return t('admin_encryption.stopped_disabled')
    return null
  })

  async function onRetry() {
    retrying.value = true
    try {
      const { data } = await retryFailedEncryption()
      status.value = data
      ui.pushToast(t('admin_encryption.retry_toast'), 'success')
    } catch (err) {
      ui.pushToast(describe(err), 'error')
    } finally {
      retrying.value = false
    }
  }

  function ask(enabled: boolean) {
    target.value = enabled
    stepUpError.value = null
    stepUpOpen.value = true
  }

  async function onConfirm(password: string) {
    saving.value = true
    stepUpError.value = null
    try {
      const { data } = await updateEncryptionSettings({
        enabled: target.value,
        acknowledge_key_custody: target.value ? acknowledged.value : undefined,
        password,
      })
      status.value = data
      stepUpOpen.value = false
      acknowledged.value = false
      ui.pushToast(
        t(data.enabled ? 'admin_encryption.saved_on' : 'admin_encryption.saved_off'),
        'success',
      )
    } catch (err) {
      stepUpError.value = describe(err)
    } finally {
      saving.value = false
    }
  }

  onMounted(() => void load())
</script>

<template>
  <div class="policy-page" data-density="operator">
    <AdminPageHeader />
    <p class="fh-field-help intro">{{ t('admin_encryption.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="status">
      <section class="block">
        <h2 class="h2">{{ t('admin_encryption.status_title') }}</h2>
        <dl class="kv" data-testid="encryption-status">
          <dt>{{ t('admin_encryption.state_label') }}</dt>
          <dd>
            <span
              class="fh-pill"
              :data-state="status.enabled ? 'active' : undefined"
              data-testid="encryption-state"
            >
              {{
                status.enabled ? t('admin_encryption.state_on') : t('admin_encryption.state_off')
              }}
            </span>
          </dd>

          <dt>{{ t('admin_encryption.backend_label') }}</dt>
          <dd>{{ backendLabel }}</dd>

          <dt>{{ t('admin_encryption.files_encrypted') }}</dt>
          <dd class="fh-mono" data-testid="encryption-files-encrypted">
            {{ status.files.encrypted }}
          </dd>

          <dt>{{ t('admin_encryption.files_plaintext') }}</dt>
          <dd class="fh-mono" data-testid="encryption-files-plaintext">
            {{
              t('admin_encryption.files_plaintext_size', {
                n: status.files.plaintext,
                size: formatBytes(status.files.plaintext_bytes),
              })
            }}
          </dd>

          <template v-if="status.files.awaiting_encryption > 0">
            <dt>{{ t('admin_encryption.files_awaiting') }}</dt>
            <dd class="fh-mono">{{ status.files.awaiting_encryption }}</dd>
          </template>

          <dt>{{ t('admin_encryption.attachments') }}</dt>
          <dd class="fh-mono">
            {{ status.inbound_attachments.encrypted }} / {{ status.inbound_attachments.plaintext }}
          </dd>

          <dt>{{ t('admin_encryption.last_run_label') }}</dt>
          <dd data-testid="encryption-last-run">
            <template v-if="status.last_run">
              <span class="fh-mono run-when">{{
                formatInSiteTime(status.last_run.finished_at, locale)
              }}</span>
              <span class="run-summary">
                {{
                  t('admin_encryption.last_run_summary', {
                    encrypted: status.last_run.encrypted,
                    remaining: status.last_run.remaining,
                  })
                }}
                <template v-if="status.last_run.failed > 0">
                  · {{ t('admin_encryption.last_run_failed', { n: status.last_run.failed }) }}
                </template>
                <span v-if="stoppedLabel" class="fh-field-help stopped">({{ stoppedLabel }})</span>
              </span>
            </template>
            <template v-else>{{ t('admin_encryption.last_run_never') }}</template>
          </dd>

          <template v-if="status.deferred !== 0">
            <dt>{{ t('admin_encryption.deferred_label') }}</dt>
            <dd data-testid="encryption-deferred">
              <template v-if="status.deferred === null">{{
                t('admin_encryption.deferred_unknown')
              }}</template>
              <template v-else>
                <span class="fh-mono">{{ status.deferred }}</span>
                <button
                  type="button"
                  class="fh-btn-text inline-action"
                  :disabled="retrying"
                  data-testid="encryption-retry"
                  @click="onRetry"
                >
                  {{ t('admin_encryption.retry') }}
                </button>
              </template>
            </dd>
          </template>

          <template v-if="status.pending_purges > 0">
            <dt>{{ t('admin_encryption.pending_purges') }}</dt>
            <dd class="fh-mono">
              {{ status.pending_purges }}
              <span v-if="status.failed_purges > 0" class="fh-field-help">
                ({{ t('admin_encryption.failed_purges') }}: {{ status.failed_purges }})
              </span>
            </dd>
          </template>
        </dl>
      </section>

      <p
        v-if="status.enabled && !status.backfill_task_enabled"
        class="fh-notice task-off"
        role="alert"
        data-tone="error"
        data-testid="encryption-task-off"
      >
        {{ t('admin_encryption.task_off') }}
        <RouterLink :to="{ name: 'admin-scheduled-tasks' }">{{
          t('admin_encryption.task_link')
        }}</RouterLink>
      </p>

      <section class="block">
        <h2 class="h2">{{ t('admin_encryption.how_title') }}</h2>
        <ul class="how">
          <li>{{ t('admin_encryption.how_new') }}</li>
          <li>{{ t('admin_encryption.how_existing') }}</li>
          <li>{{ t('admin_encryption.how_downloads') }}</li>
        </ul>
        <p v-if="status.backend === 's3'" class="fh-notice">
          {{ t('admin_encryption.s3_note') }}
        </p>
      </section>

      <section v-if="!status.enabled" class="block">
        <div class="fh-notice custody" data-tone="accent" data-testid="encryption-custody">
          <strong>{{ t('admin_encryption.custody_title') }}</strong>
          <p>{{ t('admin_encryption.custody_body') }}</p>
          <label class="toggle">
            <input v-model="acknowledged" type="checkbox" data-testid="encryption-ack" />
            <span>{{ t('admin_encryption.custody_ack') }}</span>
          </label>
        </div>
        <div class="actions">
          <button
            type="button"
            class="fh-btn"
            :disabled="!acknowledged"
            data-testid="encryption-turn-on"
            @click="ask(true)"
          >
            {{ t('admin_encryption.turn_on') }}
          </button>
        </div>
      </section>

      <section v-else class="block">
        <p class="fh-field-help">{{ t('admin_encryption.off_help') }}</p>
        <div class="actions">
          <button
            type="button"
            class="fh-btn-ghost fh-btn"
            data-testid="encryption-turn-off"
            @click="ask(false)"
          >
            {{ t('admin_encryption.turn_off') }}
          </button>
        </div>
      </section>
    </template>

    <StepUpDialog
      :open="stepUpOpen"
      :message="t(target ? 'admin_encryption.password_on' : 'admin_encryption.password_off')"
      :confirm-label="t(target ? 'admin_encryption.turn_on' : 'admin_encryption.turn_off')"
      :busy="saving"
      :error="stepUpError"
      @confirm="onConfirm"
      @cancel="stepUpOpen = false"
    />
  </div>
</template>

<style scoped>
  .policy-page {
    max-width: none;
  }

  .intro {
    margin: var(--fh-space-2) 0 var(--fh-space-4);
    max-width: 64ch;
  }

  .loading {
    color: var(--fh-subtle);
    padding: var(--fh-space-4) 0;
  }

  .block {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-3);
    max-width: 72ch;
    margin-bottom: var(--fh-space-5);
  }

  .h2 {
    font-family: var(--fh-font-display);
    font-size: 1.5rem;
    font-weight: 400;
    letter-spacing: -0.01em;
    margin: 0;
    color: var(--fh-ink);
  }

  .kv {
    display: grid;
    grid-template-columns: max-content 1fr;
    column-gap: var(--fh-space-4);
    row-gap: var(--fh-space-2);
    margin: 0;
  }

  .kv dt {
    color: var(--fh-subtle);
    font-size: var(--fh-text-body-sm);
  }

  .kv dd {
    margin: 0;
  }

  .run-when,
  .run-summary {
    display: block;
  }

  .stopped {
    margin-left: var(--fh-space-1);
  }

  .inline-action {
    margin-left: var(--fh-space-2);
  }

  .task-off {
    max-width: 72ch;
  }

  .how {
    margin: 0;
    padding-left: 1.25rem;
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-2);
  }

  .custody {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-2);
  }

  .custody p {
    margin: 0;
  }

  .toggle {
    display: inline-flex;
    align-items: flex-start;
    gap: var(--fh-space-2);
    cursor: pointer;
  }

  .toggle input {
    margin-top: 0.2rem;
  }

  .actions {
    display: flex;
    gap: var(--fh-space-3);
  }

  @media (max-width: 640px) {
    .kv {
      grid-template-columns: 1fr;
      row-gap: var(--fh-space-1);
    }
    .kv dd {
      margin-bottom: var(--fh-space-2);
    }
  }
</style>
