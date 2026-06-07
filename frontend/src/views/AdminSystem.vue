<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  applyRollback,
  applyUpdate,
  cancelPendingUpdate,
  checkUpdatesNow,
  forcePendingUpdate,
  getSystemStatus,
  getTransferActivity,
  getUpdaterJob,
  getUpdaterStatus,
  runLiveChecks,
  type SystemStatusResponse,
  type TransferActivity,
  type UpdaterJob,
  type UpdaterStatus,
} from '@/api/admin'
import { getStreamToken } from '@/api/notifications'
import { useApiError } from '@/composables/useApiError'
import { useSSE } from '@/composables/useSSE'
import { useUiStore } from '@/stores/ui'
import { formatInSiteTime } from '@/utils/datetime'

const { t, locale } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const status = ref<SystemStatusResponse | null>(null)
const loading = ref(true)
const errorMsg = ref<string | null>(null)
const refreshedAt = ref<Date | null>(null)
const liveConnected = ref(false)

// --- Self-update state ---
const updaterStatus = ref<UpdaterStatus | null>(null)
const updaterUnreachable = ref(false)
const activeJob = ref<UpdaterJob | null>(null)
const logPre = ref<HTMLElement | null>(null)

// Follow the newest log line as it streams in (sticky-bottom: don't yank the
// view if the operator has scrolled up to read earlier output).
watch(
  () => activeJob.value?.log_tail?.length ?? 0,
  async () => {
    const el = logPre.value
    const stick = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 48
    await nextTick()
    if (stick && logPre.value) logPre.value.scrollTop = logPre.value.scrollHeight
  },
)
const confirming = ref<null | 'update' | 'rollback' | 'force'>(null)
const passwordInput = ref('')
const confirmError = ref<string | null>(null)
const submitting = ref(false)
const checking = ref(false)
let jobPollHandle: ReturnType<typeof setInterval> | null = null

// --- Maintenance / drain-before-update ---
const transferActivity = ref<TransferActivity | null>(null)
const pendingUpdate = computed(() => transferActivity.value?.pending_update ?? null)
const activeTransfers = computed(
  () =>
    (transferActivity.value?.active_uploads ?? 0) +
    (transferActivity.value?.active_downloads ?? 0),
)
let activityPollHandle: ReturnType<typeof setInterval> | null = null

async function loadTransferActivity() {
  try {
    const { data } = await getTransferActivity()
    transferActivity.value = data
    // Poll while an update is postponed so the banner counts down + clears when
    // the drain worker fires the update.
    if (data.pending_update) startActivityPoll()
    else stopActivityPoll()
  } catch {
    // updater/admin endpoint unreachable - leave the existing state.
  }
}

function startActivityPoll() {
  if (activityPollHandle) return
  activityPollHandle = setInterval(() => void loadTransferActivity(), 5000)
}

function stopActivityPoll() {
  if (activityPollHandle) clearInterval(activityPollHandle)
  activityPollHandle = null
}

async function onCancelPending() {
  try {
    await cancelPendingUpdate()
    ui.pushToast(t('admin_system.update.postpone.cancelled'), 'success')
    await loadTransferActivity()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

async function onCheckNow() {
  checking.value = true
  try {
    const { data } = await checkUpdatesNow()
    if (!data.ok) {
      ui.pushToast(
        t('admin_system.update.check_now_toast.error', {
          err: data.error ?? 'unknown',
        }),
        'error',
      )
      return
    }
    // Refresh status so the version card picks up the new cache.
    await load()
    if (data.latest_version && data.latest_version !== status.value?.version.running) {
      ui.pushToast(
        t('admin_system.update.check_now_toast.found', { v: data.latest_version }),
        'success',
      )
    } else {
      ui.pushToast(
        t('admin_system.update.check_now_toast.none'),
        'success',
      )
    }
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    checking.value = false
  }
}

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getSystemStatus()
    status.value = data
    refreshedAt.value = new Date()
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

// --- Live-check trigger ---
const liveRunning = ref(false)

async function onRunLiveChecks() {
  liveRunning.value = true
  try {
    const { data } = await runLiveChecks()
    if (status.value) status.value.live = data.live
    ui.pushToast(t('admin_system.live.rerun_done'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    liveRunning.value = false
  }
}

// Debounce so a burst of cron events doesn't fire N reloads in a
// single tick (the next cron run-finished event arrives before this
// reload's response lands).
let reloadTimer: ReturnType<typeof setTimeout> | null = null
function scheduleReload() {
  if (reloadTimer) clearTimeout(reloadTimer)
  reloadTimer = setTimeout(() => {
    reloadTimer = null
    void load()
  }, 500)
}

const sse = useSSE({
  async url() {
    const { data } = await getStreamToken()
    return `/api/admin/system/stream?token=${encodeURIComponent(data.token)}`
  },
  onMessage() {
    // We don't try to reconstruct the table from the event payload -
    // simpler to re-fetch the whole status and let the existing
    // render path do its thing.
    scheduleReload()
  },
  onOpen() {
    liveConnected.value = true
  },
  onError() {
    liveConnected.value = false
  },
})

async function loadUpdaterStatus() {
  try {
    const { data } = await getUpdaterStatus()
    updaterStatus.value = data
    updaterUnreachable.value = false
    if (data.job_in_progress && activeJob.value?.id !== data.job_in_progress) {
      void pollJob(data.job_in_progress)
    }
  } catch {
    // 503 here = updater container not deployed yet. Hide the buttons
    // gracefully rather than spamming errors.
    updaterUnreachable.value = true
  }
}

async function pollJob(jobId: string) {
  if (jobPollHandle) clearInterval(jobPollHandle)
  const tick = async () => {
    try {
      const { data } = await getUpdaterJob(jobId)
      activeJob.value = data
      if (data.state === 'healthy' || data.state === 'failed' || data.state === 'rolled_back') {
        if (jobPollHandle) clearInterval(jobPollHandle)
        jobPollHandle = null
        // Backend reports the new running_version after restart - refresh.
        void load()
        void loadUpdaterStatus()
        if (data.state === 'healthy') {
          ui.pushToast(
            t('admin_system.update.toast.done', { tag: data.target_tag }),
            'success',
          )
        } else if (data.state === 'rolled_back') {
          // Self-heal: the update failed but the updater restored the previous
          // version automatically. Prod is up - warn, don't alarm.
          ui.pushToast(
            t('admin_system.update.toast.rolled_back', {
              tag: data.target_tag,
              prev: data.previous_tag ?? '-',
            }),
            'warn',
          )
        } else {
          ui.pushToast(
            t('admin_system.update.toast.failed', {
              err: data.error ?? 'unknown',
            }),
            'error',
          )
        }
      }
    } catch {
      // Network blip - try again on next tick.
    }
  }
  await tick()
  jobPollHandle = setInterval(tick, 2000)
}

async function openConfirm(kind: 'update' | 'rollback' | 'force') {
  confirming.value = kind
  passwordInput.value = ''
  confirmError.value = null
  if (kind === 'update') {
    // Refresh the in-flight transfer counts so the dialog can offer "postpone".
    await loadTransferActivity()
  }
}

function closeConfirm() {
  if (submitting.value) return
  confirming.value = null
  passwordInput.value = ''
  confirmError.value = null
}

async function submitConfirm(postpone = false) {
  if (!confirming.value || !passwordInput.value) return
  submitting.value = true
  confirmError.value = null
  try {
    if (confirming.value === 'update') {
      const tag = status.value?.version.latest
      if (!tag) {
        ui.pushToast(t('admin_system.update.toast.no_target'), 'error')
        return
      }
      const { data } = await applyUpdate(passwordInput.value, tag, postpone)
      if (data.postponed) {
        ui.pushToast(t('admin_system.update.postpone.enabled_toast'), 'success')
        await loadTransferActivity()
      } else if (data.job_id) {
        void pollJob(data.job_id)
      }
    } else if (confirming.value === 'force') {
      const { data } = await forcePendingUpdate(passwordInput.value)
      await loadTransferActivity()
      if (data.job_id) void pollJob(data.job_id)
    } else {
      const { data } = await applyRollback(passwordInput.value)
      void pollJob(data.job_id)
    }
    confirming.value = null
    passwordInput.value = ''
  } catch (err) {
    // Inline error in the modal (the toast alone was easy to miss, and a
    // wrong password left the modal looking like nothing happened).
    confirmError.value = describe(err)
    ui.pushToast(describe(err), 'error')
  } finally {
    submitting.value = false
  }
}

const jobInFlight = computed(
  () =>
    activeJob.value !== null &&
    ['queued', 'pulling', 'restarting', 'rolling_back'].includes(activeJob.value.state),
)

onMounted(() => {
  void load()
  void loadUpdaterStatus()
  void loadTransferActivity()
  sse.start()
})

onBeforeUnmount(() => {
  if (jobPollHandle) clearInterval(jobPollHandle)
  stopActivityPoll()
})

function fmtTime(iso: string | null): string {
  return formatInSiteTime(iso, locale.value, { second: '2-digit' })
}

function statusClass(s: string): string {
  if (s === 'ok') return 'pill ok'
  if (s === 'skipped') return 'pill warn'
  return 'pill danger'
}

/** True when the cron returned a soft-failure dict (the function caught
 * an error and returned `{ok: false, ...}` instead of raising). Today
 * only `release_check` does this; the convention generalizes to any
 * future cron that swallows transient errors but wants the UI to know. */
function softFailed(c: SystemStatusResponse['crons'][number]): boolean {
  const r = c.last_run?.result_summary as { ok?: boolean } | null | undefined
  return r?.ok === false
}

const headlineFailures = computed(() => {
  if (!status.value) return 0
  return status.value.crons.reduce(
    (acc, c) => acc + c.last_24h.failure + (softFailed(c) ? 1 : 0),
    0,
  )
})
</script>

<template>
  <section class="system-page">
    <header class="page-header">
      <span class="fh-eyebrow">{{ t('admin_system.eyebrow') }}</span>
      <p class="page-sub">{{ t('admin_system.sub') }}</p>
      <div class="actions">
        <button class="btn-secondary" :disabled="loading" @click="load">
          {{ loading ? t('common.loading') : t('admin_system.refresh') }}
        </button>
        <span v-if="refreshedAt" class="refreshed">
          {{ t('admin_system.refreshed_at', { when: fmtTime(refreshedAt.toISOString()) }) }}
        </span>
      </div>
    </header>

    <p v-if="errorMsg" class="error">{{ errorMsg }}</p>

    <template v-if="status">
      <!-- version + update banner -->
      <section v-if="status.version" class="card">
        <div class="card-header">
          <h2>{{ t('admin_system.version.heading') }}</h2>
          <button
            type="button"
            class="btn-secondary"
            :disabled="checking"
            @click="onCheckNow"
          >
            {{ checking ? t('common.loading') : t('admin_system.update.check_now') }}
          </button>
        </div>
        <dl class="kv-grid">
          <dt>{{ t('admin_system.version.running') }}</dt>
          <dd>
            <span class="fh-mono">{{ status.version.running }}</span>
            <span v-if="status.version.sha && status.version.sha !== 'unknown'" class="sha">
              ({{ status.version.sha.slice(0, 12) }})
            </span>
            <a
              v-if="status.version.running_release_url"
              :href="status.version.running_release_url"
              target="_blank"
              rel="noopener noreferrer"
              class="changelog-link"
            >
              {{ t('admin_system.version.changelog') }} ↗
            </a>
          </dd>
          <dt>{{ t('admin_system.version.latest') }}</dt>
          <dd>
            <span v-if="status.version.latest" class="fh-mono">{{ status.version.latest }}</span>
            <span v-else class="muted">{{ t('admin_system.version.never_checked') }}</span>
            <span v-if="status.version.last_check_at" class="sha">
              · {{ t('admin_system.version.checked', { when: fmtTime(status.version.last_check_at) }) }}
            </span>
            <span
              v-if="status.version.last_success_at && status.version.last_success_at !== status.version.last_check_at"
              class="sha"
            >
              · {{ t('admin_system.version.last_success', { when: fmtTime(status.version.last_success_at) }) }}
            </span>
            <a
              v-if="status.version.release_url"
              :href="status.version.release_url"
              target="_blank"
              rel="noopener noreferrer"
              class="changelog-link"
            >
              {{ t('admin_system.version.changelog') }} ↗
            </a>
          </dd>
          <template v-if="status.version.last_check_error">
            <dt>{{ t('admin_system.version.error') }}</dt>
            <dd><span class="error-line">{{ status.version.last_check_error }}</span></dd>
          </template>
        </dl>

        <div v-if="status.version.update_available" class="update-banner">
          <div class="banner-text">
            <strong>{{ t('admin_system.version.update_available_title', { v: status.version.latest }) }}</strong>
            <p v-if="status.version.release_published_at" class="banner-sub">
              {{ t('admin_system.version.published', { when: fmtTime(status.version.release_published_at) }) }}
            </p>
          </div>
          <div class="banner-actions">
            <button
              v-if="!updaterUnreachable && !jobInFlight"
              type="button"
              class="btn-primary"
              @click="openConfirm('update')"
            >
              {{ t('admin_system.update.btn_update', { v: status.version.latest }) }}
            </button>
            <a
              v-if="status.version.release_url"
              :href="status.version.release_url"
              target="_blank"
              rel="noopener noreferrer"
              class="btn-secondary"
            >
              {{ t('admin_system.version.view_release') }} ↗
            </a>
          </div>
        </div>

        <div v-if="pendingUpdate && !jobInFlight" class="pending-banner">
          <div class="banner-text">
            <strong>{{ t('admin_system.update.postpone.pending_title', { v: pendingUpdate.target_tag }) }}</strong>
            <p class="banner-sub">
              {{ t('admin_system.update.postpone.waiting', {
                up: transferActivity?.active_uploads ?? 0,
                down: transferActivity?.active_downloads ?? 0,
              }) }}
            </p>
            <p class="banner-sub">
              {{ t('admin_system.update.postpone.deadline', { when: fmtTime(pendingUpdate.deadline_iso) }) }}
            </p>
          </div>
          <div class="banner-actions">
            <button type="button" class="btn-primary" @click="openConfirm('force')">
              {{ t('admin_system.update.postpone.force_now') }}
            </button>
            <button type="button" class="btn-secondary" @click="onCancelPending">
              {{ t('admin_system.update.postpone.cancel') }}
            </button>
          </div>
        </div>

        <div
          v-if="updaterStatus?.rollback_target && !jobInFlight"
          class="rollback-row"
        >
          <span class="rollback-text">
            {{ t('admin_system.update.rollback_available', { v: updaterStatus.rollback_target }) }}
          </span>
          <button type="button" class="btn-secondary" @click="openConfirm('rollback')">
            {{ t('admin_system.update.btn_rollback') }}
          </button>
        </div>

        <div v-if="activeJob" class="job-banner" :data-state="activeJob.state">
          <div class="job-header">
            <strong>
              {{
                activeJob.state === 'healthy'
                  ? t('admin_system.update.job.done', { tag: activeJob.target_tag })
                  : activeJob.state === 'failed'
                  ? t('admin_system.update.job.failed', { tag: activeJob.target_tag })
                  : t(`admin_system.update.job.${activeJob.state}`, { tag: activeJob.target_tag })
              }}
            </strong>
            <span v-if="activeJob.error" class="error-line">{{ activeJob.error }}</span>
          </div>
          <details v-if="activeJob.log_tail.length > 0" class="job-log" open>
            <summary>{{ t('admin_system.update.job.log') }}</summary>
            <pre ref="logPre">{{ activeJob.log_tail.join('\n') }}</pre>
          </details>
        </div>

        <details
          v-if="status.version.update_available && status.version.release_notes"
          class="release-notes"
        >
          <summary>{{ t('admin_system.version.release_notes') }}</summary>
          <pre>{{ status.version.release_notes }}</pre>
        </details>
      </section>

      <!-- live -->
      <section class="card">
        <div class="card-header">
          <h2>{{ t('admin_system.live.heading') }}</h2>
          <button
            type="button"
            class="btn-secondary"
            :disabled="liveRunning"
            @click="onRunLiveChecks"
          >
            {{ liveRunning ? t('common.loading') : t('admin_system.live.rerun') }}
          </button>
        </div>
        <p v-if="status.live.checked_at" class="fh-field-help live-checked">
          {{ t('admin_system.live.checked', { when: fmtTime(status.live.checked_at) }) }}
        </p>
        <dl class="kv-grid">
          <dt>{{ t('admin_system.live.db') }}</dt>
          <dd>
            <span :class="statusClass(status.live.db.status)">{{ status.live.db.status }}</span>
            <span v-if="status.live.db.error" class="error-line">{{ status.live.db.error }}</span>
          </dd>
          <dt>{{ t('admin_system.live.redis') }}</dt>
          <dd>
            <span :class="statusClass(status.live.redis.status)">{{ status.live.redis.status }}</span>
            <span v-if="status.live.redis.error" class="error-line">{{ status.live.redis.error }}</span>
          </dd>
          <dt>{{ t('admin_system.live.av') }}</dt>
          <dd>
            <span :class="statusClass(status.live.av.status)">{{ status.live.av.status }}</span>
            <span v-if="status.live.av.error" class="error-line">{{ status.live.av.error }}</span>
          </dd>
        </dl>
      </section>

      <!-- headline counters -->
      <section class="counters">
        <div class="counter">
          <span class="counter-num">{{ headlineFailures }}</span>
          <span class="counter-label">{{ t('admin_system.counters.cron_failures_24h') }}</span>
        </div>
        <div class="counter">
          <span class="counter-num">{{ status.email_undeliverable_24h }}</span>
          <span class="counter-label">{{ t('admin_system.counters.email_undeliverable_24h') }}</span>
        </div>
      </section>

      <!-- Scheduled tasks (crons) live on their own page now. -->
      <section class="card">
        <h2>{{ t('admin_system.crons.heading') }}</h2>
        <p class="muted">
          {{ t('admin_system.crons.moved') }}
          <RouterLink :to="{ name: 'admin-scheduled-tasks' }">
            {{ t('admin.nav.scheduled_tasks') }}
          </RouterLink>
        </p>
      </section>

      <!-- update / rollback confirm modal -->
      <div
        v-if="confirming"
        class="fh-modal-backdrop"
        role="dialog"
        aria-modal="true"
        @click.self="closeConfirm"
        @keydown.escape="closeConfirm"
      >
        <div class="fh-modal fh-modal--small">
          <h2 class="modal-h2">
            {{
              confirming === 'update'
                ? t('admin_system.update.confirm.title_update', { v: status.version.latest })
                : confirming === 'force'
                ? t('admin_system.update.postpone.force_title', { v: pendingUpdate?.target_tag })
                : t('admin_system.update.confirm.title_rollback', {
                    v: updaterStatus?.rollback_target,
                  })
            }}
          </h2>
          <p class="modal-body">{{ t('admin_system.update.confirm.body') }}</p>
          <div
            v-if="confirming === 'update' && activeTransfers > 0"
            class="fh-notice"
            data-tone="warning"
          >
            {{ t('admin_system.update.postpone.active_notice', {
              up: transferActivity?.active_uploads ?? 0,
              down: transferActivity?.active_downloads ?? 0,
            }) }}
          </div>
          <form @submit.prevent="submitConfirm(false)">
            <label class="fh-field">
              <span class="fh-field-label">{{ t('common.current_password') }}</span>
              <input
                v-model="passwordInput"
                type="password"
                class="fh-field-input"
                autocomplete="current-password"
                required
                autofocus
              />
            </label>
            <div
              v-if="confirmError"
              class="fh-notice"
              data-tone="error"
              role="alert"
            >
              {{ confirmError }}
            </div>
            <div class="form-actions">
              <button
                type="submit"
                class="btn-primary"
                :disabled="submitting || !passwordInput"
              >
                {{ submitting
                  ? t('common.loading')
                  : confirming === 'update' && activeTransfers > 0
                  ? t('admin_system.update.postpone.update_now_anyway')
                  : t('admin_system.update.confirm.action') }}
              </button>
              <button
                v-if="confirming === 'update' && activeTransfers > 0"
                type="button"
                class="btn-secondary"
                :disabled="submitting || !passwordInput"
                @click="submitConfirm(true)"
              >
                {{ t('admin_system.update.postpone.postpone_btn') }}
              </button>
              <button
                type="button"
                class="btn-secondary"
                :disabled="submitting"
                @click="closeConfirm"
              >
                {{ t('common.cancel') }}
              </button>
            </div>
          </form>
        </div>
      </div>

      <!-- recent failures -->
      <section v-if="status.recent_failures.length > 0" class="card">
        <h2>{{ t('admin_system.failures.heading') }}</h2>
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ t('admin_system.failures.job') }}</th>
              <th>{{ t('admin_system.failures.at') }}</th>
              <th>{{ t('admin_system.failures.error') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in status.recent_failures" :key="r.id">
              <td><code>{{ r.job_name }}</code></td>
              <td>{{ fmtTime(r.started_at) }}</td>
              <td class="error-cell">
                <pre>{{ r.error_msg }}</pre>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </section>
</template>

<style scoped>
.system-page {
  padding: var(--fh-space-4) 0;
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-5);
}
.page-header h1 { margin: 0 0 var(--fh-space-2); }
.page-sub { color: var(--fh-subtle); margin: 0 0 var(--fh-space-3); }
.actions { display: flex; align-items: center; gap: var(--fh-space-3); }
.refreshed { color: var(--fh-subtle); font-size: var(--fh-text-body-sm); }
.card {
  background: var(--fh-paper-raised);
  padding: var(--fh-space-4);
  border: 1px solid var(--fh-hairline);
}
.card h2 { margin: 0 0 var(--fh-space-3); font-size: var(--fh-text-body-lg); }
.kv-grid {
  display: grid;
  grid-template-columns: 100px 1fr;
  gap: var(--fh-space-2) var(--fh-space-3);
  margin: 0;
}
.kv-grid dt { color: var(--fh-subtle); }
.kv-grid dd { margin: 0; }
.error-line { color: var(--fh-danger); margin-left: var(--fh-space-2); font-family: var(--fh-font-mono); font-size: var(--fh-text-mono-sm); }
.sha { color: var(--fh-subtle); font-family: var(--fh-font-mono); font-size: var(--fh-text-mono-sm); margin-left: var(--fh-space-2); }
.changelog-link { color: var(--fh-accent); font-size: var(--fh-text-body-sm); margin-left: var(--fh-space-2); text-decoration: none; white-space: nowrap; }
.changelog-link:hover { text-decoration: underline; }
.run-btn { padding: 2px 10px; font-size: var(--fh-text-body-sm); white-space: nowrap; }
.live-checked { margin: 0 0 var(--fh-space-2); }
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fh-space-3);
  margin-bottom: var(--fh-space-3);
}
.card-header h2 { margin: 0; }
.update-banner {
  margin-top: var(--fh-space-3);
  padding: var(--fh-space-3) var(--fh-space-4);
  background: #fff8e1;
  border: 1px solid #f1c40f;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fh-space-3);
}
.pending-banner {
  margin-top: var(--fh-space-3);
  padding: var(--fh-space-3) var(--fh-space-4);
  background: #fff3cd;
  border: 1px solid #ffeeba;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fh-space-3);
}
.banner-text strong { display: block; }
.banner-sub { margin: var(--fh-space-1) 0 0; color: var(--fh-subtle); font-size: var(--fh-text-body-sm); }
.release-notes {
  margin-top: var(--fh-space-3);
  font-size: var(--fh-text-body-sm);
}
.release-notes summary { cursor: pointer; color: var(--fh-subtle); }
.release-notes pre {
  margin: var(--fh-space-2) 0 0;
  white-space: pre-wrap;
  max-height: 300px;
  overflow: auto;
  background: var(--fh-paper);
  padding: var(--fh-space-2);
  border: 1px solid var(--fh-hairline);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
}
.banner-actions { display: flex; gap: var(--fh-space-2); align-items: center; }
.rollback-row {
  margin-top: var(--fh-space-3);
  padding: var(--fh-space-2) var(--fh-space-3);
  background: var(--fh-paper-raised);
  border: 1px solid var(--fh-hairline);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--fh-space-3);
}
.rollback-text { color: var(--fh-subtle); font-size: var(--fh-text-body-sm); }
.job-banner {
  margin-top: var(--fh-space-3);
  padding: var(--fh-space-3) var(--fh-space-4);
  border: 1px solid var(--fh-hairline);
  background: var(--fh-paper-raised);
}
.job-banner[data-state="failed"] { background: #f8d7da; border-color: #f5c6cb; }
.job-banner[data-state="healthy"] { background: #d4edda; border-color: #c3e6cb; }
/* Self-heal: update failed but the previous version was restored - amber, not red. */
.job-banner[data-state="rolling_back"],
.job-banner[data-state="rolled_back"] { background: #fff3cd; border-color: #ffeeba; }
.job-header { display: flex; flex-direction: column; gap: var(--fh-space-1); }
.job-log { margin-top: var(--fh-space-2); }
.job-log summary { cursor: pointer; color: var(--fh-subtle); font-size: var(--fh-text-body-sm); }
.job-log pre {
  margin: var(--fh-space-2) 0 0;
  white-space: pre-wrap;
  max-height: 240px;
  overflow: auto;
  background: var(--fh-paper);
  padding: var(--fh-space-2);
  border: 1px solid var(--fh-hairline);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
}
.btn-primary {
  padding: var(--fh-space-2) var(--fh-space-3);
  background: var(--fh-accent);
  color: var(--fh-paper);
  border: 1px solid var(--fh-accent);
  cursor: pointer;
  font: inherit;
}
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.fh-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(26, 29, 36, 0.4);
  display: grid;
  place-items: center;
  z-index: 100;
}
.fh-modal {
  background: var(--fh-paper);
  border: 1px solid var(--fh-hairline-strong);
  box-shadow: 0 8px 40px rgba(26, 29, 36, 0.15);
  padding: var(--fh-space-5);
  width: min(480px, 92vw);
  max-height: 92vh;
  overflow-y: auto;
}
.fh-modal--small { width: min(420px, 92vw); }
.modal-h2 { font-family: var(--fh-font-display); font-size: 1.25rem; margin: 0 0 var(--fh-space-3); }
.modal-body { margin: 0 0 var(--fh-space-3); color: var(--fh-ink); font-size: var(--fh-text-body-sm); }
.form-actions { display: flex; gap: var(--fh-space-3); align-items: baseline; margin-top: var(--fh-space-3); }
.counters { display: flex; gap: var(--fh-space-4); }
.counter {
  background: var(--fh-paper-raised);
  padding: var(--fh-space-4);
  border: 1px solid var(--fh-hairline);
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
}
.counter-num { font-family: var(--fh-font-display); font-size: 2.5rem; line-height: 1; }
.counter-label { color: var(--fh-subtle); font-size: var(--fh-text-body-sm); }
.data-table {
  width: 100%;
  border-collapse: collapse;
}
.data-table th, .data-table td {
  text-align: left;
  padding: var(--fh-space-2) var(--fh-space-3);
  border-bottom: 1px solid var(--fh-hairline);
  font-size: var(--fh-text-body-sm);
  vertical-align: top;
}
.data-table th { color: var(--fh-subtle); font-weight: normal; }
.result code, td code {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  word-break: break-all;
}
.pill {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 3px;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  margin-right: 4px;
}
.pill.ok { background: #d4edda; color: #155724; }
.pill.warn { background: #fff3cd; color: #856404; }
.pill.danger { background: #f8d7da; color: #721c24; }
.muted { color: var(--fh-subtle); }
.error { color: var(--fh-danger); }
.error-cell pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  max-height: 200px;
  overflow: auto;
}
.btn-secondary {
  padding: var(--fh-space-2) var(--fh-space-3);
  background: var(--fh-paper);
  border: 1px solid var(--fh-hairline);
  cursor: pointer;
}
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
