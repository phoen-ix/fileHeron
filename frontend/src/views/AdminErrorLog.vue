<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { exportErrorCsv, listErrorLog } from '@/api/admin'
import Pager from '@/components/Pager.vue'
import { useApiError } from '@/composables/useApiError'
import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import type { AdminErrorRow } from '@/types/api'
import { siteLocalIsoToUtcIso } from '@/utils/datetime'
import { downloadBlob } from '@/utils/downloadBlob'

const { t } = useI18n()
const { formatDate } = useSiteDateFormat()
const { describe, describeBlob } = useApiError()
const ui = useUiStore()

const items = ref<AdminErrorRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const code = ref('')
const statusCode = ref('')
const source = ref('')
const ip = ref('')
const fromTs = ref('')
const toTs = ref('')

const loading = ref(true)
const errorMsg = ref<string | null>(null)
const expanded = ref<number | null>(null)

// Every value the backend can write to error_log.source. `csp` was added with
// the report sink and missed here, so CSP violations were stored and then
// invisible in the one screen the rollout decision ("enforce once the reports
// come back empty") is read from - the reports came back empty because nobody
// could filter for them (audit #2, B1).
const sourceOptions = ['', 'http', 'spa', 'worker', 'csp']

function statusTone(s: number): 'active' | 'warn' | 'danger' | undefined {
  if (s >= 500) return 'danger'
  if (s >= 400) return 'warn'
  return undefined
}

const filterParams = computed(() => {
  const p: Record<string, string> = {}
  if (code.value) p.code = code.value
  if (statusCode.value) p.status_code = statusCode.value
  if (source.value) p.source = source.value
  if (ip.value) p.ip = ip.value
  // `datetime-local` yields a bare wall-clock string. Sent as-is it was
  // compared as naive UTC, so in a site timezone of UTC+2 a filter set to the
  // moment shown on a row excluded that row and the next two hours - the
  // investigator saw an empty table and the same hole landed in the CSV
  // export (audit #2). Convert to an instant, interpreting the picker's value
  // in the site timezone, exactly as the display does.
  if (fromTs.value) p.from = siteLocalIsoToUtcIso(fromTs.value)
  if (toTs.value) p.to = siteLocalIsoToUtcIso(toTs.value)
  return p
})

// Out-of-order guard. Typing in the filter fires a request per keystroke
// (debounced, not serialised), and whichever response arrived LAST won - so a
// slow early request could overwrite the results of a newer, narrower one and
// leave the table showing rows that do not match what is in the search box
// (audit 2026-07-30, fe-correct-11). Same `seq` pattern usePaginatedList uses.
let loadSeq = 0

async function load() {
  const mine = ++loadSeq
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await listErrorLog({
      ...filterParams.value,
      status_code: statusCode.value ? Number(statusCode.value) : undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    if (mine !== loadSeq) return
    items.value = data.items
    total.value = data.total
  } catch (err) {
    if (mine !== loadSeq) return
    errorMsg.value = describe(err)
  } finally {
    if (mine === loadSeq) loading.value = false
  }
}

useDebouncedSearch(filterParams, () => {
  page.value = 1
  void load()
})
watch(page, load)

function toggle(id: number) {
  expanded.value = expanded.value === id ? null : id
}

const exporting = ref(false)
async function onExportCsv() {
  exporting.value = true
  try {
    const { data } = await exportErrorCsv(filterParams.value)
    downloadBlob(data as Blob, 'error-log.csv')
  } catch (err) {
    ui.pushToast(await describeBlob(err), 'error')
  } finally {
    exporting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <h1 class="fh-eyebrow">{{ t('admin_error_log.eyebrow') }}</h1>
        <p class="fh-field-help intro">{{ t('admin_error_log.intro') }}</p>
      </div>
      <button type="button" class="fh-btn fh-btn-ghost" :disabled="exporting" @click="onExportCsv">
        {{ t('admin_error_log.export_csv') }}
      </button>
    </div>

    <hr class="fh-rule" />

    <div class="filters">
      <input
        v-model.trim="code" class="fh-field-input" :placeholder="t('admin_error_log.filter.code')"
        :aria-label="t('admin_error_log.filter.code')"
/>
      <input
        v-model.trim="ip" class="fh-field-input" :placeholder="t('admin_error_log.filter.ip')"
        :aria-label="t('admin_error_log.filter.ip')"
/>
      <input
        v-model.trim="statusCode"
        :aria-label="t('admin_error_log.filter.status')"
        class="fh-field-input"
        type="number"
        min="100"
        max="599"
        :placeholder="t('admin_error_log.filter.status')"
      />
      <select
        v-model="source" class="fh-field-input"
        :aria-label="t('common.filter')"
      >
        <option v-for="s in sourceOptions" :key="s" :value="s">
          {{ s ? t(`admin_error_log.source.${s}`) : t('admin_error_log.filter.source_any') }}
        </option>
      </select>
      <input
        v-model="fromTs" class="fh-field-input" type="datetime-local" :title="t('admin_error_log.filter.from')"
        :aria-label="t('admin_error_log.filter.from')"
/>
      <input
        v-model="toTs" class="fh-field-input" type="datetime-local" :title="t('admin_error_log.filter.to')"
        :aria-label="t('admin_error_log.filter.to')"
/>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div
v-else-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>
    <div v-else-if="items.length === 0" class="loading">{{ t('admin_error_log.empty') }}</div>

    <table v-else class="error-table">
      <thead>
        <tr>
          <th>{{ t('admin_error_log.col.when') }}</th>
          <th>{{ t('admin_error_log.col.ip') }}</th>
          <th>{{ t('admin_error_log.col.status') }}</th>
          <th>{{ t('admin_error_log.col.code') }}</th>
          <th>{{ t('admin_error_log.col.where') }}</th>
          <th>{{ t('admin_error_log.col.message') }}</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="r in items" :key="r.id">
          <!-- Keyboard path for a click-only row: see AdminInbox.vue. -->
          <tr
            class="row"
            tabindex="0"
            role="button"
            :aria-expanded="expanded === r.id"
            :aria-label="t('admin_error_log.toggle_row', { code: r.code })"
            @click="toggle(r.id)"
            @keydown.enter.prevent="toggle(r.id)"
            @keydown.space.prevent="toggle(r.id)"
          >
            <td class="fh-mono nowrap">{{ formatDate(r.created_at, { second: '2-digit' }) }}</td>
            <td class="fh-mono nowrap">{{ r.ip ?? '-' }}</td>
            <td>
              <span class="fh-pill" :data-state="statusTone(r.status_code)">{{ r.status_code }}</span>
              <span v-if="r.alerted" class="fh-pill mini" data-state="active">{{ t('admin_error_log.emailed') }}</span>
            </td>
            <td class="fh-mono">{{ r.code }}</td>
            <td class="fh-mono where">{{ r.source === 'worker' ? r.job_name : `${r.method ?? ''} ${r.path ?? ''}`.trim() }}</td>
            <td class="msg">{{ r.message ?? '-' }}</td>
          </tr>
          <tr v-if="expanded === r.id" class="detail-row">
            <td colspan="6">
              <dl class="detail">
                <dt>{{ t('admin_error_log.detail.exception') }}</dt><dd class="fh-mono">{{ r.exception_type ?? '-' }}</dd>
                <dt>{{ t('admin_error_log.detail.source') }}</dt><dd class="fh-mono">{{ r.source }}</dd>
                <dt>{{ t('admin_error_log.detail.ip') }}</dt><dd class="fh-mono">{{ r.ip ?? '-' }}</dd>
                <dt>{{ t('admin_error_log.detail.request_id') }}</dt><dd class="fh-mono">{{ r.request_id ?? '-' }}</dd>
                <dt>{{ t('admin_error_log.detail.user') }}</dt>
                <dd class="fh-mono">{{ r.user_id !== null ? `#${r.user_id}${r.auth_via ? ` (${r.auth_via})` : ''}` : '-' }}</dd>
                <dt>{{ t('admin_error_log.detail.signature') }}</dt><dd class="fh-mono">{{ r.signature }}</dd>
                <dt>{{ t('admin_error_log.detail.message') }}</dt><dd class="msg-full">{{ r.message ?? '-' }}</dd>
              </dl>
            </td>
          </tr>
        </template>
      </tbody>
    </table>

    <Pager v-model:page="page" :total="total" :page-size="pageSize" />
  </div>
</template>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--fh-space-4);
}

.intro {
  margin: var(--fh-space-2) 0 0;
  max-width: 60ch;
}

.filters {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--fh-space-2);
  margin-bottom: var(--fh-space-4);
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}

.error-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fh-text-body-sm);
}

.error-table th {
  text-align: left;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
  font-weight: 500;
  padding: var(--fh-space-2) var(--fh-space-3) var(--fh-space-2) 0;
  border-bottom: var(--fh-border);
}

.error-table td {
  padding: var(--fh-space-2) var(--fh-space-3) var(--fh-space-2) 0;
  border-bottom: var(--fh-border);
  vertical-align: top;
}

.row {
  cursor: pointer;
}

.row:hover td {
  background: var(--fh-paper-sunk);
}

.nowrap {
  white-space: nowrap;
}

.where {
  max-width: 22rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msg {
  max-width: 28rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fh-pill.mini {
  margin-left: var(--fh-space-2);
  font-size: 10px;
}

.detail-row td {
  background: var(--fh-paper-sunk);
}

.detail {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--fh-space-1) var(--fh-space-4);
  margin: 0;
  padding: var(--fh-space-2) 0;
}

.detail dt {
  color: var(--fh-subtle);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
}

.detail dd {
  margin: 0;
}

.msg-full {
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
