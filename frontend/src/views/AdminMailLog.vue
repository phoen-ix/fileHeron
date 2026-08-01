<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { exportMailCsv, listMailLog } from '@/api/admin'
import Pager from '@/components/Pager.vue'
import { useApiError } from '@/composables/useApiError'
import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import type { AdminMailRow } from '@/types/api'
import { siteLocalIsoToUtcIso } from '@/utils/datetime'
import { downloadBlob } from '@/utils/downloadBlob'

const { t } = useI18n()
const { formatDate } = useSiteDateFormat()
const { describe, describeBlob } = useApiError()
const ui = useUiStore()
const route = useRoute()

const items = ref<AdminMailRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const q = ref('')
const category = ref('')
const status = ref('')
const recipientEmail = ref('')
const fromTs = ref('')
const toTs = ref('')
// Set when arriving via a "View all emails to this user" deep-link.
const recipientUserId = ref<number | null>(null)

const loading = ref(true)
const errorMsg = ref<string | null>(null)

const statusOptions = ['', 'queued', 'sent', 'failed', 'error']

function statusTone(s: string): 'active' | 'warn' | 'danger' | undefined {
  if (s === 'sent') return 'active'
  if (s === 'queued') return 'warn'
  if (s === 'failed' || s === 'error') return 'danger'
  return undefined
}

const filterParams = computed(() => {
  const p: Record<string, string> = {}
  if (q.value) p.q = q.value
  if (category.value) p.category = category.value
  if (status.value) p.status = status.value
  if (recipientEmail.value) p.recipient_email = recipientEmail.value
  if (recipientUserId.value !== null) p.recipient_user_id = String(recipientUserId.value)
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
    const { data } = await listMailLog({
      ...filterParams.value,
      recipient_user_id: recipientUserId.value ?? undefined,
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

const exporting = ref(false)
async function onExportCsv() {
  exporting.value = true
  try {
    const { data } = await exportMailCsv(filterParams.value)
    downloadBlob(data as Blob, 'mail-log.csv')
  } catch (err) {
    ui.pushToast(await describeBlob(err), 'error')
  } finally {
    exporting.value = false
  }
}

onMounted(() => {
  const ruid = route.query.recipient_user_id
  if (typeof ruid === 'string' && ruid) recipientUserId.value = Number(ruid)
  const remail = route.query.recipient_email
  if (typeof remail === 'string' && remail) recipientEmail.value = remail
  void load()
})
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <span class="fh-eyebrow">{{ t('admin_mail.eyebrow') }}</span>
        <p class="fh-field-help intro">{{ t('admin_mail.intro') }}</p>
      </div>
      <button type="button" class="fh-btn fh-btn-ghost" :disabled="exporting" @click="onExportCsv">
        {{ t('admin_mail.export_csv') }}
      </button>
    </div>

    <hr class="fh-rule" />

    <div class="filters">
      <input v-model.trim="q" class="fh-field-input" :placeholder="t('admin_mail.filter.q')" />
      <input v-model.trim="recipientEmail" class="fh-field-input" :placeholder="t('admin_mail.filter.recipient')" />
      <input v-model.trim="category" class="fh-field-input" :placeholder="t('admin_mail.filter.category')" />
      <select v-model="status" class="fh-field-input">
        <option v-for="s in statusOptions" :key="s" :value="s">
          {{ s ? t(`admin_mail.status.${s}`) : t('admin_mail.filter.status_any') }}
        </option>
      </select>
      <input v-model="fromTs" class="fh-field-input" type="datetime-local" :title="t('admin_mail.filter.from')" />
      <input v-model="toTs" class="fh-field-input" type="datetime-local" :title="t('admin_mail.filter.to')" />
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>
    <div v-else-if="items.length === 0" class="loading">{{ t('admin_mail.empty') }}</div>

    <table v-else class="mail-table">
      <thead>
        <tr>
          <th>{{ t('admin_mail.col.when') }}</th>
          <th>{{ t('admin_mail.col.recipient') }}</th>
          <th>{{ t('admin_mail.col.category') }}</th>
          <th>{{ t('admin_mail.col.subject') }}</th>
          <th>{{ t('admin_mail.col.status') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in items" :key="r.id">
          <td class="fh-mono nowrap">{{ formatDate(r.created_at, { second: '2-digit' }) }}</td>
          <td class="recipient-cell">
            <RouterLink
              v-if="r.recipient_user_id !== null"
              :to="{ name: 'admin-user-detail', params: { id: r.recipient_user_id } }"
              class="recipient-link"
            >
              <span class="recipient-name">{{ r.recipient_display_name ?? `#${r.recipient_user_id}` }}</span>
              <span class="recipient-hint fh-mono">{{ r.recipient_email }}</span>
            </RouterLink>
            <span v-else class="fh-mono">{{ r.recipient_email }}</span>
          </td>
          <td class="fh-mono">{{ r.category ?? '-' }}</td>
          <td>
            <RouterLink
              :to="{ name: 'admin-mail-detail', params: { id: r.id } }"
              class="subject-link"
            >
              {{ r.subject }}
            </RouterLink>
            <span v-if="r.masked" class="fh-pill mini" data-state="warn">{{ t('admin_mail.masked') }}</span>
          </td>
          <td>
            <span class="fh-pill" :data-state="statusTone(r.status)">{{ t(`admin_mail.status.${r.status}`) }}</span>
            <span v-if="r.smtp_code" class="fh-mono code">{{ r.smtp_code }}</span>
          </td>
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

.mail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fh-text-body-sm);
}

.mail-table th {
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

.mail-table td {
  padding: var(--fh-space-2) var(--fh-space-3) var(--fh-space-2) 0;
  border-bottom: var(--fh-border);
  vertical-align: top;
}

.nowrap {
  white-space: nowrap;
}

.recipient-cell {
  max-width: 18rem;
}

.recipient-link {
  display: flex;
  flex-direction: column;
  gap: 1px;
  text-decoration: none;
  color: inherit;
}

.recipient-link:hover .recipient-name {
  color: var(--fh-accent);
}

.recipient-hint {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.subject-link {
  color: var(--fh-ink);
  text-decoration: none;
}

.subject-link:hover {
  color: var(--fh-accent);
  text-decoration: underline;
}

.fh-pill.mini {
  margin-left: var(--fh-space-2);
  font-size: 10px;
}

.code {
  margin-left: var(--fh-space-2);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}
</style>
