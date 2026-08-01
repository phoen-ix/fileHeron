<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { exportAuditCsv, listAuditLog } from '@/api/admin'
import Pager from '@/components/Pager.vue'
import { useApiError } from '@/composables/useApiError'
import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import type { AdminAuditRow } from '@/types/api'
import { siteLocalIsoToUtcIso } from '@/utils/datetime'
import { downloadBlob } from '@/utils/downloadBlob'

const { t } = useI18n()
const { formatDate } = useSiteDateFormat()
const { describe, describeBlob } = useApiError()
const ui = useUiStore()

const items = ref<AdminAuditRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const eventType = ref('')
const targetType = ref('')
const targetId = ref('')
const fromTs = ref('')
const toTs = ref('')

const loading = ref(true)
const errorMsg = ref<string | null>(null)

const filterParams = computed(() => {
  const p: Record<string, string> = {}
  if (eventType.value) p.event_type = eventType.value
  if (targetType.value) p.target_type = targetType.value
  if (targetId.value) p.target_id = targetId.value
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
    const { data } = await listAuditLog({
      ...filterParams.value,
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

// Filters reset to page 1 then reload (debounced); page changes reload directly.
useDebouncedSearch(filterParams, () => {
  page.value = 1
  void load()
})
watch(page, load)

const exporting = ref(false)
async function onExportCsv() {
  exporting.value = true
  try {
    const { data } = await exportAuditCsv(filterParams.value)
    downloadBlob(data as Blob, 'audit-log.csv')
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
        <h1 class="fh-eyebrow">{{ t('admin_audit.eyebrow') }}</h1>
      </div>
      <button type="button" class="fh-btn fh-btn-ghost" :disabled="exporting" @click="onExportCsv">
        {{ t('admin_audit.export_csv') }}
      </button>
    </div>

    <hr class="fh-rule" />

    <div class="filters">
      <input
        v-model.trim="eventType" class="fh-field-input" :placeholder="t('admin_audit.filter.event_type')"
        :aria-label="t('admin_audit.filter.event_type')"
/>
      <input
        v-model.trim="targetType" class="fh-field-input" :placeholder="t('admin_audit.filter.target_type')"
        :aria-label="t('admin_audit.filter.target_type')"
/>
      <input
        v-model.trim="targetId" class="fh-field-input" :placeholder="t('admin_audit.filter.target_id')"
        :aria-label="t('admin_audit.filter.target_id')"
/>
      <input
        v-model="fromTs" class="fh-field-input" type="datetime-local" :title="t('admin_audit.filter.from')"
        :aria-label="t('admin_audit.filter.from')"
/>
      <input
        v-model="toTs" class="fh-field-input" type="datetime-local" :title="t('admin_audit.filter.to')"
        :aria-label="t('admin_audit.filter.to')"
/>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div
v-else-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>

    <table v-else class="audit-table">
      <thead>
        <tr>
          <th>{{ t('admin_audit.col.when') }}</th>
          <th>{{ t('admin_audit.col.event') }}</th>
          <th>{{ t('admin_audit.col.actor') }}</th>
          <th>{{ t('admin_audit.col.target') }}</th>
          <th>{{ t('admin_audit.col.ip') }}</th>
          <th>{{ t('admin_audit.col.request') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in items" :key="r.id">
          <td class="fh-mono nowrap">{{ formatDate(r.created_at, { second: '2-digit' }) }}</td>
          <td><span class="fh-mono ev">{{ r.event_type }}</span></td>
          <td class="actor-cell">
            <template v-if="r.actor_user_id !== null">
              <RouterLink
                :to="{ name: 'admin-user-detail', params: { id: r.actor_user_id } }"
                class="actor-link"
              >
                <span v-if="r.actor_display_name" class="actor-name">{{ r.actor_display_name }}</span>
                <span v-else class="actor-name">
                  #{{ r.actor_user_id }}
                  <span class="actor-deleted">{{ t('admin_audit.actor_deleted') }}</span>
                </span>
                <span v-if="r.actor_email" class="actor-hint fh-mono">{{ r.actor_email }}</span>
              </RouterLink>
            </template>
            <span v-else class="fh-mono">-</span>
          </td>
          <td class="fh-mono">
            <span v-if="r.target_type">{{ r.target_type }}:{{ r.target_id }}</span>
            <span v-else>-</span>
          </td>
          <td class="fh-mono">{{ r.ip ?? '-' }}</td>
          <td class="fh-mono small">{{ r.request_id?.slice(0, 8) ?? '-' }}</td>
        </tr>
      </tbody>
    </table>

    <Pager v-model:page="page" :total="total" :page-size="pageSize" />
  </div>
</template>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: var(--fh-space-4);
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

.audit-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fh-text-body-sm);
}

.audit-table th {
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

.audit-table td {
  padding: var(--fh-space-2) var(--fh-space-3) var(--fh-space-2) 0;
  border-bottom: var(--fh-border);
  vertical-align: top;
}

.nowrap {
  white-space: nowrap;
}

.ev {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-ink);
}

.small {
  font-size: 11px;
}

.actor-cell {
  max-width: 18rem;
}

.actor-link {
  display: flex;
  flex-direction: column;
  gap: 1px;
  text-decoration: none;
  color: inherit;
}

.actor-link:hover .actor-name {
  color: var(--fh-accent);
}

.actor-name {
  color: var(--fh-ink);
}

.actor-hint {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.actor-deleted {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
  font-family: var(--fh-font-mono);
  margin-left: var(--fh-space-1);
}

</style>
