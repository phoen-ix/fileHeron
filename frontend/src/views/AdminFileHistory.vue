<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { adminDeleteFile, adminListFiles, adminReclaimFile } from '@/api/admin'
import Pager from '@/components/Pager.vue'
import { useApiError } from '@/composables/useApiError'
import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
import { useTableSort } from '@/composables/useTableSort'
import { useUiStore } from '@/stores/ui'
import type { AdminFileItem, FileState, ShareState } from '@/types/api'
import { formatBytes } from '@/utils/bytes'
import { formatInSiteTime } from '@/utils/datetime'
import { shareStatePill } from '@/utils/statePill'

const { t, locale } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()
const route = useRoute()

const items = ref<AdminFileItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const loading = ref(true)
const errorMsg = ref<string | null>(null)

const q = ref('')
const stateFilter = ref<FileState | ''>('')
const shareStateFilter = ref<ShareState | ''>('')
const orphanedOnly = ref(false)
const includeInactive = ref(false)
// Deep-link from the admin user-detail "View in File History" link.
const uploaderId = ref<number | null>(
  route.query.uploader_id ? Number(route.query.uploader_id) : null,
)
const reclaiming = ref<string | null>(null)
const deleting = ref<string | null>(null)

const sort = useTableSort({ defaultBy: 'uploaded_at', defaultDir: 'desc' })

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await adminListFiles({
      q: q.value || undefined,
      state: stateFilter.value || undefined,
      share_state: shareStateFilter.value || undefined,
      orphaned: orphanedOnly.value || undefined,
      include_inactive: includeInactive.value || undefined,
      uploader_id: uploaderId.value ?? undefined,
      sort: sort.sortBy.value,
      direction: sort.sortDir.value,
      page: page.value,
      page_size: pageSize.value,
    })
    items.value = data.items
    total.value = data.total
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

useDebouncedSearch(q, () => {
  page.value = 1
  void load()
})
watch([stateFilter, shareStateFilter, orphanedOnly, includeInactive], () => {
  page.value = 1
  void load()
})

function clearUploaderFilter() {
  uploaderId.value = null
  page.value = 1
  void load()
}

async function onReclaim(it: AdminFileItem) {
  if (reclaiming.value) return
  if (!(await ui.confirm({ message: t('admin_file_history.reclaim_confirm', { name: it.filename }), danger: true }))) return
  reclaiming.value = it.file_id
  try {
    await adminReclaimFile(it.file_id)
    ui.pushToast(t('admin_file_history.reclaimed_toast', { name: it.filename }), 'success')
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    reclaiming.value = null
  }
}

async function onDelete(it: AdminFileItem) {
  if (deleting.value) return
  if (!(await ui.confirm({ message: t('admin_file_history.delete_confirm', { name: it.filename }), danger: true }))) return
  deleting.value = it.file_id
  try {
    await adminDeleteFile(it.file_id)
    ui.pushToast(t('admin_file_history.deleted_toast', { name: it.filename }), 'success')
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    deleting.value = null
  }
}
watch([sort.sortBy, sort.sortDir, page], load)

function formatDate(iso: string | null): string {
  return formatInSiteTime(iso, locale.value)
}

function pillForFileState(s: FileState): string | undefined {
  if (s === 'clean') return 'active'
  if (s === 'deleted' || s === 'infected') return 'danger'
  if (s === 'ready_unscanned') return 'warn'
  return undefined
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <span class="fh-eyebrow">{{ t('admin_file_history.eyebrow') }}</span>
      </div>
      <span class="fh-mono total-count">{{ t('admin_file_history.total_count', { n: total }) }}</span>
    </div>

    <hr class="fh-rule" />

    <p class="fh-field-help intro">{{ t('admin_file_history.intro') }}</p>

    <div class="filters">
      <input
        v-model.trim="q"
        type="search"
        class="fh-field-input search"
        :placeholder="t('admin_file_history.search_placeholder')"
      />
      <select v-model="stateFilter" class="filter-select">
        <option value="">{{ t('admin_file_history.file_state_all') }}</option>
        <option value="clean">clean</option>
        <option value="ready_unscanned">ready_unscanned</option>
        <option value="infected">infected</option>
        <option value="deleted">deleted</option>
        <option value="uploading">uploading</option>
      </select>
      <select v-model="shareStateFilter" class="filter-select">
        <option value="">{{ t('admin_file_history.share_state_all') }}</option>
        <option value="active">{{ t('share_state.active') }}</option>
        <option value="expired">{{ t('share_state.expired') }}</option>
        <option value="revoked">{{ t('share_state.revoked') }}</option>
        <option value="deleted">{{ t('share_state.deleted') }}</option>
      </select>
      <label class="orphan-toggle">
        <input v-model="orphanedOnly" type="checkbox" />
        {{ t('admin_file_history.orphaned_only') }}
      </label>
      <label class="orphan-toggle">
        <input v-model="includeInactive" type="checkbox" />
        {{ t('admin_file_history.show_inactive') }}
      </label>
      <button
        v-if="uploaderId !== null"
        type="button"
        class="fh-btn-text uploader-chip"
        @click="clearUploaderFilter"
      >
        {{ t('admin_file_history.uploader_filter', { id: uploaderId }) }} ✕
      </button>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

    <table v-else-if="items.length > 0" class="files-table">
      <thead>
        <tr>
          <th
            role="button"
            tabindex="0"
            :aria-sort="sort.ariaSort('filename')"
            @click="sort.toggle('filename')"
            @keydown.enter="sort.toggle('filename')"
          >
            {{ t('admin_file_history.col.filename') }}
            <span class="sort-ind">{{ sort.indicator('filename') }}</span>
          </th>
          <th
            role="button"
            tabindex="0"
            class="numeric"
            :aria-sort="sort.ariaSort('size')"
            @click="sort.toggle('size')"
            @keydown.enter="sort.toggle('size')"
          >
            {{ t('admin_file_history.col.size') }}
            <span class="sort-ind">{{ sort.indicator('size') }}</span>
          </th>
          <th
            role="button"
            tabindex="0"
            :aria-sort="sort.ariaSort('state')"
            @click="sort.toggle('state')"
            @keydown.enter="sort.toggle('state')"
          >
            {{ t('admin_file_history.col.state') }}
            <span class="sort-ind">{{ sort.indicator('state') }}</span>
          </th>
          <th>{{ t('admin_file_history.col.uploader') }}</th>
          <th>{{ t('admin_file_history.col.share') }}</th>
          <th
            role="button"
            tabindex="0"
            :aria-sort="sort.ariaSort('uploaded_at')"
            @click="sort.toggle('uploaded_at')"
            @keydown.enter="sort.toggle('uploaded_at')"
          >
            {{ t('admin_file_history.col.uploaded') }}
            <span class="sort-ind">{{ sort.indicator('uploaded_at') }}</span>
          </th>
          <th
            role="button"
            tabindex="0"
            :aria-sort="sort.ariaSort('last_downloaded_at')"
            @click="sort.toggle('last_downloaded_at')"
            @keydown.enter="sort.toggle('last_downloaded_at')"
          >
            {{ t('admin_file_history.col.last_dl') }}
            <span class="sort-ind">{{ sort.indicator('last_downloaded_at') }}</span>
          </th>
          <th
            role="button"
            tabindex="0"
            class="numeric"
            :aria-sort="sort.ariaSort('download_count')"
            @click="sort.toggle('download_count')"
            @keydown.enter="sort.toggle('download_count')"
          >
            {{ t('admin_file_history.col.dl_count') }}
            <span class="sort-ind">{{ sort.indicator('download_count') }}</span>
          </th>
          <th>{{ t('admin_file_history.col.actions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="it in items" :key="it.file_id">
          <td>
            <div class="row-name">{{ it.filename }}</div>
            <div class="fh-mono row-hint">{{ it.recipients_summary }}</div>
          </td>
          <td class="numeric fh-mono">{{ formatBytes(it.size_bytes) }}</td>
          <td>
            <span class="fh-pill" :data-state="pillForFileState(it.state)">
              {{ it.state }}
            </span>
            <span v-if="it.is_orphaned" class="fh-pill orphan-badge" data-state="warn">
              {{ t('admin_file_history.orphaned_badge') }}
            </span>
          </td>
          <td>
            <div class="row-name">{{ it.uploader.display_name }}</div>
            <div class="fh-mono row-hint">{{ it.uploader.email }} · {{ it.uploader.role }}</div>
          </td>
          <td>
            <div class="row-name">{{ it.share_subject || t('share_list.no_subject') }}</div>
            <div class="fh-mono row-hint">
              <span class="fh-pill" :data-state="shareStatePill(it.share_state)">
                {{ t(`share_state.${it.share_state}`) }}
              </span>
            </div>
          </td>
          <td class="fh-mono">{{ formatDate(it.uploaded_at) }}</td>
          <td class="fh-mono">{{ formatDate(it.last_downloaded_at) }}</td>
          <td class="numeric fh-mono">{{ it.download_count }}</td>
          <td>
            <button
              v-if="it.is_orphaned"
              type="button"
              class="fh-btn-text reclaim-btn"
              :disabled="reclaiming === it.file_id"
              @click="onReclaim(it)"
            >
              {{ reclaiming === it.file_id ? t('common.loading') : t('admin_file_history.reclaim') }}
            </button>
            <button
              v-else-if="it.state !== 'deleted'"
              type="button"
              class="fh-btn-text reclaim-btn"
              :disabled="deleting === it.file_id"
              @click="onDelete(it)"
            >
              {{ deleting === it.file_id ? t('common.loading') : t('admin_file_history.delete') }}
            </button>
            <span v-else class="row-hint">—</span>
          </td>
        </tr>
      </tbody>
    </table>

    <p v-else class="fh-field-help empty">{{ t('admin_file_history.empty') }}</p>

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

.total-count {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.intro {
  margin: var(--fh-space-2) 0 var(--fh-space-3);
  max-width: 64ch;
}

.filters {
  display: flex;
  gap: var(--fh-space-3);
  margin-bottom: var(--fh-space-4);
  align-items: baseline;
  flex-wrap: wrap;
}

.search {
  flex: 1;
  max-width: 360px;
}

.filter-select {
  font: inherit;
  background: transparent;
  border: var(--fh-border-strong);
  border-radius: var(--fh-radius-sm);
  padding: 4px 8px;
  color: var(--fh-ink);
}

.orphan-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-2);
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
  cursor: pointer;
}

.orphan-badge {
  margin-left: var(--fh-space-2);
}

.reclaim-btn {
  color: var(--fh-accent);
  white-space: nowrap;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}

.files-table {
  width: 100%;
  border-collapse: collapse;
}

.files-table th,
.files-table td {
  text-align: left;
  padding: var(--fh-space-2) var(--fh-space-3);
  border-bottom: 1px solid var(--fh-rule);
  vertical-align: top;
}

.files-table th {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--fh-subtle);
  font-weight: 500;
  user-select: none;
}

.files-table th[role="button"] {
  cursor: pointer;
}

.files-table th[role="button"]:hover {
  color: var(--fh-ink);
}

.sort-ind {
  display: inline-block;
  width: 1ch;
  margin-left: 2px;
  color: var(--fh-accent);
}

.row-name {
  font-weight: 500;
}

.row-hint {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.numeric {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.empty {
  margin: var(--fh-space-3) 0;
}
</style>
