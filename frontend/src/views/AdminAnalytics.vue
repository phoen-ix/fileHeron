<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { exportAnalyticsCsv, getAnalytics } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { linePoints, scaleBars } from '@/composables/useAnalyticsCharts'
import { useUiStore } from '@/stores/ui'
import type { AnalyticsDayPoint, AnalyticsResponse } from '@/types/api'
import { formatBytes } from '@/utils/bytes'
import { downloadBlob } from '@/utils/downloadBlob'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const data = ref<AnalyticsResponse | null>(null)
const loading = ref(true)
const errorMsg = ref<string | null>(null)
const days = ref(30)
const downloading = ref(false)

const RANGES = [7, 30, 90]
const CHART_W = 600
const CHART_H = 120

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data: body } = await getAnalytics(days.value)
    data.value = body
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

watch(days, load)
onMounted(load)

function total(points: AnalyticsDayPoint[]): number {
  return points.reduce((a, p) => a + p.count, 0)
}

const storageLine = computed(() =>
  linePoints(
    (data.value?.storage_trend ?? []).map((s) => s.storage_bytes),
    CHART_W,
    CHART_H,
  ),
)
const storageMax = computed(() => {
  const vals = (data.value?.storage_trend ?? []).map((s) => s.storage_bytes)
  return vals.length ? formatBytes(Math.max(...vals)) : '-'
})
const sharesBars = computed(() =>
  scaleBars((data.value?.shares_created ?? []).map((p) => p.count), CHART_W, CHART_H),
)
const downloadsBars = computed(() =>
  scaleBars((data.value?.downloads ?? []).map((p) => p.count), CHART_W, CHART_H),
)
const avBars = computed(() =>
  scaleBars((data.value?.av_quarantines ?? []).map((p) => p.count), CHART_W, CHART_H),
)
const fileStates = computed(() =>
  Object.entries(data.value?.file_states ?? {})
    .map(([state, count]) => ({ state, count }))
    .sort((a, b) => b.count - a.count),
)
const fileStateMax = computed(() => Math.max(1, ...fileStates.value.map((s) => s.count)))

async function onExport() {
  downloading.value = true
  try {
    const { data: blob } = await exportAnalyticsCsv(days.value)
    downloadBlob(blob as Blob, `analytics-${days.value}d.csv`)
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    downloading.value = false
  }
}
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <span class="fh-eyebrow">{{ t('admin_analytics.eyebrow') }}</span>
        <h1 class="fh-h1">{{ t('admin_analytics.title') }}</h1>
      </div>
      <div class="header-actions">
        <div class="range-toggle" role="group" :aria-label="t('admin_analytics.range_label')">
          <button
            v-for="r in RANGES"
            :key="r"
            type="button"
            class="range-btn"
            :class="{ active: days === r }"
            @click="days = r"
          >
            {{ t('admin_analytics.range_days', { n: r }) }}
          </button>
        </div>
        <button
          type="button"
          class="fh-btn fh-btn-ghost"
          :disabled="downloading || !data"
          @click="onExport"
        >
          {{ t('admin_analytics.export_csv') }}
        </button>
      </div>
    </div>

    <hr class="fh-rule" />

    <div v-if="loading" class="fh-notice">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="data">
      <!-- Storage trend -->
      <section class="card">
        <div class="card-head">
          <h2 class="card-h2">{{ t('admin_analytics.storage_trend') }}</h2>
          <span class="card-meta fh-mono">
            {{ t('admin_analytics.peak', { v: storageMax }) }}
            <template v-if="data.storage_as_of">
              · {{ t('admin_analytics.storage_as_of', { d: data.storage_as_of }) }}
            </template>
          </span>
        </div>
        <svg v-if="data.storage_trend.length" class="chart" :viewBox="`0 0 ${CHART_W} ${CHART_H}`">
          <polyline :points="storageLine" class="line" />
        </svg>
        <p v-else class="empty fh-field-help">{{ t('admin_analytics.no_storage_yet') }}</p>
      </section>

      <!-- Activity series -->
      <div class="grid-3">
        <section class="card">
          <div class="card-head">
            <h2 class="card-h2">{{ t('admin_analytics.shares_created') }}</h2>
            <span class="card-meta fh-mono">{{ total(data.shares_created) }}</span>
          </div>
          <svg class="chart" :viewBox="`0 0 ${CHART_W} ${CHART_H}`">
            <rect v-for="(b, i) in sharesBars" :key="i" :x="b.x" :y="b.y" :width="b.width" :height="b.height" class="bar" />
          </svg>
        </section>
        <section class="card">
          <div class="card-head">
            <h2 class="card-h2">{{ t('admin_analytics.downloads') }}</h2>
            <span class="card-meta fh-mono">{{ total(data.downloads) }}</span>
          </div>
          <svg class="chart" :viewBox="`0 0 ${CHART_W} ${CHART_H}`">
            <rect v-for="(b, i) in downloadsBars" :key="i" :x="b.x" :y="b.y" :width="b.width" :height="b.height" class="bar" />
          </svg>
        </section>
        <section class="card">
          <div class="card-head">
            <h2 class="card-h2">{{ t('admin_analytics.av_quarantines') }}</h2>
            <span class="card-meta fh-mono">{{ total(data.av_quarantines) }}</span>
          </div>
          <svg class="chart" :viewBox="`0 0 ${CHART_W} ${CHART_H}`">
            <rect v-for="(b, i) in avBars" :key="i" :x="b.x" :y="b.y" :width="b.width" :height="b.height" class="bar danger" />
          </svg>
        </section>
      </div>

      <!-- File states -->
      <section class="card">
        <h2 class="card-h2">{{ t('admin_analytics.file_states') }}</h2>
        <ul class="state-list">
          <li v-for="s in fileStates" :key="s.state">
            <span class="state-name fh-mono">{{ s.state }}</span>
            <span class="state-track">
              <span class="state-fill" :style="{ width: (s.count / fileStateMax) * 100 + '%' }" />
            </span>
            <span class="state-count fh-mono">{{ s.count }}</span>
          </li>
        </ul>
      </section>

      <!-- Top tables -->
      <div class="grid-2">
        <section class="card">
          <h2 class="card-h2">{{ t('admin_analytics.top_uploaders') }}</h2>
          <table class="data-table">
            <thead>
              <tr><th>{{ t('admin_analytics.col_user') }}</th><th class="num">{{ t('admin_analytics.col_stored') }}</th></tr>
            </thead>
            <tbody>
              <tr v-for="u in data.top_uploaders" :key="u.user_id">
                <td><span class="cell-name">{{ u.display_name }}</span><span class="cell-sub fh-mono">{{ u.email }}</span></td>
                <td class="num fh-mono">{{ formatBytes(u.bytes) }}</td>
              </tr>
              <tr v-if="!data.top_uploaders.length"><td colspan="2" class="empty">{{ t('admin_analytics.no_data') }}</td></tr>
            </tbody>
          </table>
        </section>
        <section class="card">
          <h2 class="card-h2">{{ t('admin_analytics.top_shares') }}</h2>
          <table class="data-table">
            <thead>
              <tr><th>{{ t('admin_analytics.col_share') }}</th><th class="num">{{ t('admin_analytics.col_downloads') }}</th></tr>
            </thead>
            <tbody>
              <tr v-for="s in data.top_shares" :key="s.share_id">
                <td><span class="cell-name">{{ s.subject || t('admin_analytics.untitled') }}</span><span class="cell-sub fh-mono">{{ s.share_id.slice(0, 8) }}</span></td>
                <td class="num fh-mono">{{ s.downloads }}</td>
              </tr>
              <tr v-if="!data.top_shares.length"><td colspan="2" class="empty">{{ t('admin_analytics.no_data') }}</td></tr>
            </tbody>
          </table>
        </section>
      </div>

      <!-- Quota warnings -->
      <section v-if="data.quota_warnings.length" class="card">
        <h2 class="card-h2">{{ t('admin_analytics.quota_warnings') }}</h2>
        <table class="data-table">
          <thead>
            <tr><th>{{ t('admin_analytics.col_user') }}</th><th class="num">{{ t('admin_analytics.col_usage') }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="q in data.quota_warnings" :key="q.user_id">
              <td><span class="cell-name">{{ q.display_name }}</span><span class="cell-sub fh-mono">{{ q.email }}</span></td>
              <td class="num fh-mono warn">
                {{ q.pct }}% · {{ formatBytes(q.used_bytes) }} / {{ formatBytes(q.quota_bytes) }}
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </div>
</template>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: var(--fh-space-3);
  flex-wrap: wrap;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: var(--fh-space-3);
}
.range-toggle {
  display: inline-flex;
  border: var(--fh-border-strong);
  border-radius: var(--fh-radius-sm);
  overflow: hidden;
}
.range-btn {
  font: inherit;
  background: transparent;
  border: none;
  padding: 4px 12px;
  cursor: pointer;
  color: var(--fh-subtle);
  border-left: var(--fh-border);
}
.range-btn:first-child {
  border-left: none;
}
.range-btn.active {
  background: var(--fh-accent-soft);
  color: var(--fh-ink);
}
.card {
  background: var(--fh-paper-raised);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-4);
  margin-top: var(--fh-space-4);
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--fh-space-3);
}
.card-h2 {
  margin: 0 0 var(--fh-space-2);
  font-family: var(--fh-font-display);
  font-size: 1.3rem;
  font-weight: 400;
}
.card-meta {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}
.grid-2,
.grid-3 {
  display: grid;
  gap: var(--fh-space-4);
}
.grid-2 {
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
}
.grid-3 {
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
}
.grid-2 .card,
.grid-3 .card {
  margin-top: 0;
}
.grid-2,
.grid-3 {
  margin-top: var(--fh-space-4);
}
.chart {
  width: 100%;
  height: auto;
  display: block;
  margin-top: var(--fh-space-2);
}
.chart .line {
  fill: none;
  stroke: var(--fh-accent);
  stroke-width: 2;
  vector-effect: non-scaling-stroke;
}
.chart .bar {
  fill: var(--fh-accent);
}
.chart .bar.danger {
  fill: var(--fh-danger, #b00020);
}
.empty {
  color: var(--fh-subtle);
  padding: var(--fh-space-2) 0;
}
.state-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}
.state-list li {
  display: grid;
  grid-template-columns: 140px 1fr 60px;
  align-items: center;
  gap: var(--fh-space-3);
}
.state-track {
  background: var(--fh-paper);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  height: 12px;
  overflow: hidden;
}
.state-fill {
  display: block;
  height: 100%;
  background: var(--fh-accent);
}
.state-count {
  text-align: right;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  margin-top: var(--fh-space-2);
}
.data-table th {
  text-align: left;
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
  font-weight: 400;
  padding: 4px 0;
  border-bottom: var(--fh-border);
}
.data-table td {
  padding: 6px 0;
  border-bottom: var(--fh-hairline-rule, 1px solid var(--fh-hairline));
  vertical-align: top;
}
.data-table .num {
  text-align: right;
}
.cell-name {
  display: block;
}
.cell-sub {
  display: block;
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
}
.warn {
  color: var(--fh-danger, #b00020);
}
</style>
