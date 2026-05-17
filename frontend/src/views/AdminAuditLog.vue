<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { auditCsvUrl, listAuditLog } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import type { AdminAuditRow } from '@/types/api'
import { formatInSiteTime } from '@/utils/datetime'

const { t, locale } = useI18n()
const { describe } = useApiError()

const items = ref<AdminAuditRow[]>([])
const pageSize = ref(50)
const eventType = ref('')
const targetType = ref('')
const targetId = ref('')
const fromTs = ref('')
const toTs = ref('')

const loading = ref(true)
const errorMsg = ref<string | null>(null)

// Cursor-based pagination. The backend gives us a `next_cursor` for
// the next older page. To go back to a previous page we pop the stack:
// `cursorStack` holds the cursors we used to ARRIVE at each page, so
// the previous page is one entry back.
const cursorStack = ref<(string | null)[]>([null])
const currentCursor = computed(() => cursorStack.value[cursorStack.value.length - 1])
const nextCursor = ref<string | null>(null)

let searchTimer: ReturnType<typeof setTimeout> | null = null

const filterParams = computed(() => {
  const p: Record<string, string> = {}
  if (eventType.value) p.event_type = eventType.value
  if (targetType.value) p.target_type = targetType.value
  if (targetId.value) p.target_id = targetId.value
  if (fromTs.value) p.from = fromTs.value
  if (toTs.value) p.to = toTs.value
  return p
})

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const params: Parameters<typeof listAuditLog>[0] = {
      ...filterParams.value,
      page_size: pageSize.value,
    }
    if (currentCursor.value !== null) params.cursor = currentCursor.value
    const { data } = await listAuditLog(params)
    items.value = data.items
    nextCursor.value = data.next_cursor
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

function resetAndReload() {
  cursorStack.value = [null]
  nextCursor.value = null
  void load()
}

function goNewer() {
  if (cursorStack.value.length <= 1) return
  cursorStack.value = cursorStack.value.slice(0, -1)
  void load()
}

function goOlder() {
  if (!nextCursor.value) return
  cursorStack.value = [...cursorStack.value, nextCursor.value]
  void load()
}

function debouncedReload() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    resetAndReload()
  }, 220)
}

watch([eventType, targetType, targetId, fromTs, toTs], debouncedReload)

const csvHref = computed(() => auditCsvUrl(filterParams.value))
const isFirstPage = computed(() => cursorStack.value.length <= 1)

function formatDate(iso: string): string {
  return formatInSiteTime(iso, locale.value, { second: '2-digit' })
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <span class="fh-eyebrow">{{ t('admin_audit.eyebrow') }}</span>
      </div>
      <a :href="csvHref" class="fh-btn fh-btn-ghost" download>
        {{ t('admin_audit.export_csv') }}
      </a>
    </div>

    <hr class="fh-rule" />

    <div class="filters">
      <input v-model.trim="eventType" class="fh-field-input" :placeholder="t('admin_audit.filter.event_type')" />
      <input v-model.trim="targetType" class="fh-field-input" :placeholder="t('admin_audit.filter.target_type')" />
      <input v-model.trim="targetId" class="fh-field-input" :placeholder="t('admin_audit.filter.target_id')" />
      <input v-model="fromTs" class="fh-field-input" type="datetime-local" :title="t('admin_audit.filter.from')" />
      <input v-model="toTs" class="fh-field-input" type="datetime-local" :title="t('admin_audit.filter.to')" />
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

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
          <td class="fh-mono nowrap">{{ formatDate(r.created_at) }}</td>
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
            <span v-else class="fh-mono">—</span>
          </td>
          <td class="fh-mono">
            <span v-if="r.target_type">{{ r.target_type }}:{{ r.target_id }}</span>
            <span v-else>—</span>
          </td>
          <td class="fh-mono">{{ r.ip ?? '—' }}</td>
          <td class="fh-mono small">{{ r.request_id?.slice(0, 8) ?? '—' }}</td>
        </tr>
      </tbody>
    </table>

    <div v-if="!isFirstPage || nextCursor" class="pager">
      <button type="button" class="fh-btn-text" :disabled="isFirstPage" @click="goNewer">
        ← {{ t('admin_audit.pager.newer') }}
      </button>
      <button
        type="button"
        class="fh-btn-text"
        :disabled="!nextCursor"
        @click="goOlder"
      >
        {{ t('admin_audit.pager.older') }} →
      </button>
    </div>
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

.pager {
  display: flex;
  gap: var(--fh-space-3);
  align-items: center;
  margin-top: var(--fh-space-4);
}

.page-info {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}
</style>
