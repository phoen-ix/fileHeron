<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getSystemStatus, type SystemStatusResponse } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'

const { t, locale } = useI18n()
const { describe } = useApiError()

const status = ref<SystemStatusResponse | null>(null)
const loading = ref(true)
const errorMsg = ref<string | null>(null)
const refreshedAt = ref<Date | null>(null)

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

onMounted(load)

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(locale.value === 'de' ? 'de-AT' : 'en-US', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function fmtDuration(ms: number | null): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function statusClass(s: string): string {
  if (s === 'ok') return 'pill ok'
  if (s === 'skipped') return 'pill warn'
  return 'pill danger'
}

function cronStatusClass(s: string): string {
  if (s === 'success') return 'pill ok'
  if (s === 'running') return 'pill warn'
  return 'pill danger'
}

const headlineFailures = computed(
  () => status.value?.crons.reduce((acc, c) => acc + c.last_24h.failure, 0) ?? 0,
)
</script>

<template>
  <section class="system-page">
    <header class="page-header">
      <h1>{{ t('admin_system.heading') }}</h1>
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
      <!-- live -->
      <section class="card">
        <h2>{{ t('admin_system.live.heading') }}</h2>
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

      <!-- crons -->
      <section class="card">
        <h2>{{ t('admin_system.crons.heading') }}</h2>
        <table class="data-table">
          <thead>
            <tr>
              <th>{{ t('admin_system.crons.job') }}</th>
              <th>{{ t('admin_system.crons.last_status') }}</th>
              <th>{{ t('admin_system.crons.last_run') }}</th>
              <th>{{ t('admin_system.crons.duration') }}</th>
              <th>{{ t('admin_system.crons.last_24h') }}</th>
              <th>{{ t('admin_system.crons.result') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in status.crons" :key="c.job_name">
              <td><code>{{ c.job_name }}</code></td>
              <td>
                <span v-if="c.last_run" :class="cronStatusClass(c.last_run.status)">
                  {{ c.last_run.status }}
                </span>
                <span v-else class="muted">{{ t('admin_system.crons.no_runs') }}</span>
              </td>
              <td>{{ c.last_run ? fmtTime(c.last_run.started_at) : '—' }}</td>
              <td>{{ c.last_run ? fmtDuration(c.last_run.duration_ms) : '—' }}</td>
              <td>
                <span class="pill ok">{{ c.last_24h.success }}</span>
                <span v-if="c.last_24h.failure > 0" class="pill danger">{{ c.last_24h.failure }}</span>
                <span v-if="c.last_24h.running > 0" class="pill warn">{{ c.last_24h.running }}</span>
              </td>
              <td class="result">
                <code v-if="c.last_run?.result_summary">{{ JSON.stringify(c.last_run.result_summary) }}</code>
                <span v-else class="muted">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

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
