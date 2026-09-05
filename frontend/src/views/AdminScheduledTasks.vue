<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getCrons, runCron, updateCronSchedule } from '@/api/admin'
import { getStreamToken } from '@/api/notifications'
import { useApiError } from '@/composables/useApiError'
import { useSSE } from '@/composables/useSSE'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import type { CronScheduleItem } from '@/types/api'

const { t } = useI18n()
const { describe } = useApiError()
const { formatDate } = useSiteDateFormat()
const ui = useUiStore()

const loading = ref(true)
const errorMsg = ref<string | null>(null)
const items = ref<CronScheduleItem[]>([])
const siteTz = ref('UTC')
// Master error-alert switch; the per-task "alert on failure" toggle is only
// shown when this is on (set on the Error alerts settings page).
const errorAlertsEnabled = ref(false)
const savingName = ref<string | null>(null)
const runningName = ref<string | null>(null)

const groups = computed(() => {
  const order: string[] = []
  const byGroup: Record<string, CronScheduleItem[]> = {}
  for (const it of items.value) {
    if (!byGroup[it.group]) {
      byGroup[it.group] = []
      order.push(it.group)
    }
    byGroup[it.group].push(it)
  }
  return order.map((g) => ({ key: g, items: byGroup[g] }))
})

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getCrons()
    items.value = data.items
    siteTz.value = data.site_timezone
    errorAlertsEnabled.value = data.error_alerts_enabled
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onSave(it: CronScheduleItem) {
  savingName.value = it.name
  // Snapshot to roll back on failure. The row is bound with v-model, so a
  // refused PUT (below `min_interval_min`, or a cleared field that 422s to the
  // generic message) left the table showing a cadence that is not in effect,
  // with the recomputed Next-run beside it - and anyone who opened the page
  // later on that tab read the same wrong number (audit #2).
  const before = { ...it }
  try {
    const { data } = await updateCronSchedule(it.name, {
      enabled: it.enabled,
      kind: it.kind,
      interval_minutes: it.interval_minutes,
      daily_time: it.daily_time,
      alert_on_failure: it.alert_on_failure,
    })
    Object.assign(it, data)
    ui.pushToast(t('admin_scheduled_tasks.saved'), 'success')
  } catch (err) {
    Object.assign(it, before)
    ui.pushToast(describe(err), 'error')
  } finally {
    savingName.value = null
  }
}

async function onRun(it: CronScheduleItem) {
  runningName.value = it.name
  try {
    await runCron(it.name)
    ui.pushToast(t('admin_scheduled_tasks.run_queued', { name: it.name }), 'success')
    setTimeout(() => void load(), 1500)
  } catch (err) {
    ui.pushToast(describe(err), 'warn')
  } finally {
    runningName.value = null
  }
}

function statusTone(it: CronScheduleItem): string {
  if (it.last_status === 'success') return 'ok'
  if (it.last_status === 'running') return 'warn'
  if (it.last_status === 'failure') return 'danger'
  return ''
}

let reloadTimer: ReturnType<typeof setTimeout> | null = null
const sse = useSSE({
  async url() {
    const { data } = await getStreamToken()
    return `/api/admin/system/stream?token=${encodeURIComponent(data.token)}`
  },
  onMessage() {
    if (reloadTimer) clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => void load(), 500)
  },
})

onMounted(() => {
  void load()
  // The stream was never started, so a row triggered by hand showed `running`
  // forever and an operator watching the page concluded the worker was wedged
  // (audit #2).
  sse.start()
})
onBeforeUnmount(() => {
  sse.stop()
  if (reloadTimer) clearTimeout(reloadTimer)
})
</script>

<template>
  <div class="cron-page" data-density="operator">
    <h1 class="fh-eyebrow">{{ t('admin.eyebrow') }} / {{ t('admin_scheduled_tasks.title') }}</h1>
    <p class="fh-field-help intro">{{ t('admin_scheduled_tasks.intro', { tz: siteTz }) }}</p>

    <div
v-if="errorMsg" class="fh-notice" role="alert"
        data-tone="danger">{{ errorMsg }}</div>
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <template v-else>
      <section v-for="grp in groups" :key="grp.key" class="group">
        <h2 class="form-h2">{{ t(`admin_scheduled_tasks.group.${grp.key}`) }}</h2>
        <div class="table-wrap">
          <table class="cron-table">
            <colgroup>
              <col class="col-task" />
              <col class="col-sched" />
              <col class="col-recent" />
              <col class="col-next" />
              <col class="col-actions" />
            </colgroup>
            <thead>
              <tr>
                <th>{{ t('admin_scheduled_tasks.col_task') }}</th>
                <th>{{ t('admin_scheduled_tasks.col_schedule') }}</th>
                <th>{{ t('admin_scheduled_tasks.col_status') }}</th>
                <th>{{ t('admin_scheduled_tasks.col_next') }}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="it in grp.items" :key="it.name">
                <td class="task-cell">
                  <label class="enable">
                    <input v-model="it.enabled" type="checkbox" />
                    <code>{{ it.name }}</code>
                  </label>
                  <span class="desc">{{ it.description }}</span>
                  <label v-if="errorAlertsEnabled" class="alert-toggle">
                    <input v-model="it.alert_on_failure" type="checkbox" />
                    <span>{{ t('admin_scheduled_tasks.alert_on_failure') }}</span>
                  </label>
                </td>
                <td class="sched-cell">
                  <div class="cell-row">
                    <select
        v-model="it.kind" class="fh-field-input kind"
        :aria-label="t('common.filter')"
      >
                      <option value="interval">{{ t('admin_scheduled_tasks.kind_interval') }}</option>
                      <option value="daily">{{ t('admin_scheduled_tasks.kind_daily') }}</option>
                    </select>
                    <span v-if="it.kind === 'interval'" class="sched-input">
                      <input
                        v-model.number="it.interval_minutes"
                        :aria-label="t('common.filter')"
                        type="number"
                        class="fh-field-input num"
                        :min="it.min_interval_minutes"
                        max="10080"
                      />
                      {{ t('admin_scheduled_tasks.minutes') }}
                    </span>
                    <span v-else class="sched-input">
                      <input
        v-model="it.daily_time" type="time" class="fh-field-input time"
        :aria-label="t('common.filter')"
/>
                    </span>
                  </div>
                </td>
                <td class="status-cell">
                  <span v-if="it.last_status" class="fh-pill" :data-tone="statusTone(it)">{{ it.last_status }}</span>
                  <span v-else class="muted">-</span>
                  <span class="counts fh-mono">
                    <span class="ok">{{ it.last_24h.success }}</span>/<span class="bad">{{ it.last_24h.failure }}</span>
                  </span>
                  <span v-if="it.last_run_at" class="last fh-mono">{{ formatDate(it.last_run_at) }}</span>
                </td>
                <td class="fh-mono next">{{ it.enabled ? formatDate(it.next_run_at) : t('admin_scheduled_tasks.disabled') }}</td>
                <td class="actions">
                  <div class="cell-row actions-row">
                    <button type="button" class="fh-btn" :disabled="savingName === it.name" @click="onSave(it)">
                      {{ t('common.save') }}
                    </button>
                    <button type="button" class="fh-btn-text" :disabled="runningName === it.name" @click="onRun(it)">
                      {{ t('admin_scheduled_tasks.run_now') }}
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.intro {
  margin: var(--fh-space-2) 0 var(--fh-space-4);
  max-width: 70ch;
}
.group + .group {
  margin-top: var(--fh-space-5);
}
.table-wrap {
  /* Narrow-screen safety only: on wide screens the table is narrower than this
     wrapper so nothing scrolls; below the table's natural width it scrolls here
     instead of breaking the page. */
  overflow-x: auto;
}
.cron-table {
  /* One shared, fixed column template across every group table so all sections
     fill the full width, end at the same right edge, and line up column-for-column.
     min-width keeps the nowrap timestamp columns from collapsing; below it the
     .table-wrap scrolls. */
  width: 100%;
  min-width: 64rem;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: var(--fh-text-body-md);
  line-height: 1.5;
}
/* Proportions sized so the nowrap content fits at min-width (the schedule cell -
   select + time input - is the widest, hence its larger share). */
.col-task {
  width: 27%;
}
.col-sched {
  width: 21%;
}
.col-recent {
  width: 18%;
}
.col-next {
  width: 18%;
}
.col-actions {
  width: 16%;
}
.cron-table th {
  text-align: left;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
  font-weight: 500;
  padding: var(--fh-space-2) var(--fh-space-4) var(--fh-space-2) 0;
  border-bottom: var(--fh-border);
  white-space: nowrap;
}
.cron-table td {
  padding: var(--fh-space-4) var(--fh-space-4) var(--fh-space-4) 0;
  border-bottom: var(--fh-border);
  vertical-align: top;
}
.cron-table tbody tr:hover td {
  background: var(--fh-paper-sunk);
}
.task-cell .enable {
  display: flex;
  align-items: center;
  gap: var(--fh-space-2);
}
.desc {
  display: block;
  color: var(--fh-ink-soft);
  font-size: var(--fh-text-body-sm);
  margin-top: var(--fh-space-2);
  max-width: 48ch;
}
.alert-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-1);
  margin-top: var(--fh-space-3);
  font-size: var(--fh-text-body-sm);
  color: var(--fh-ink-soft);
  cursor: pointer;
}
.sched-cell {
  white-space: nowrap;
}
/* Flex lives on an inner wrapper, never the <td> - a flex <td> drops out of the
   table row-height model so its border-bottom stops aligning with the other cells. */
.cell-row {
  display: flex;
  align-items: center;
  gap: var(--fh-space-2);
}
.actions-row {
  justify-content: flex-start;
}
.fh-field-input.kind {
  width: auto;
}
.fh-field-input.num {
  width: 5rem;
}
.fh-field-input.time {
  width: 7rem;
}
.status-cell {
  white-space: nowrap;
}
.counts {
  margin-left: var(--fh-space-2);
  font-size: var(--fh-text-mono-sm);
}
.counts .ok {
  color: var(--fh-success);
}
.counts .bad {
  color: var(--fh-danger);
}
.last,
.next {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-ink-soft);
}
.last {
  display: block;
  margin-top: var(--fh-space-1);
}
.next {
  white-space: nowrap;
}
.actions {
  white-space: nowrap;
}
.muted {
  color: var(--fh-ink-soft);
}
</style>
