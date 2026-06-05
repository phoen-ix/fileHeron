<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import api from '@/api/client'
import {
  adminListFiles,
  adminQuarantinePurge,
  adminQuarantineRelease,
} from '@/api/admin'
import Pager from '@/components/Pager.vue'
import { useApiError } from '@/composables/useApiError'
import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
import { usePaginatedList } from '@/composables/usePaginatedList'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import type { AdminFileItem } from '@/types/api'
import { formatBytes } from '@/utils/bytes'

const { t } = useI18n()
const { formatDate } = useSiteDateFormat()
const { describe } = useApiError()
const ui = useUiStore()

const q = ref('')

type ConfirmKind = 'release' | 'purge'

const confirm = ref<{
  kind: ConfirmKind
  file: AdminFileItem
  reason: string
  busy: boolean
} | null>(null)

const { items, total, page, pageSize, loading, errorMsg, load } =
  usePaginatedList<AdminFileItem>(({ page, pageSize }) =>
    adminListFiles({
      q: q.value || undefined,
      state: 'infected',
      sort: 'uploaded_at',
      direction: 'desc',
      page,
      page_size: pageSize,
    }).then((r) => r.data),
  )

useDebouncedSearch(q, () => {
  page.value = 1
  void load()
})
watch(page, load)


async function onDownload(file: AdminFileItem) {
  try {
    const resp = await api.get(`/admin/files/${file.file_id}/quarantine/download`, {
      responseType: 'blob',
    })
    const url = URL.createObjectURL(resp.data as Blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${file.filename}.quarantined`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (err) {
    // axios with responseType=blob delivers the error body as a Blob too,
    // so describe() can't see the envelope. Re-parse the blob as JSON
    // before handing it off so the user sees the real backend message
    // (e.g. QUARANTINE_BYTES_MISSING) instead of the generic fallback.
    const ax = err as { response?: { data?: unknown } }
    if (ax.response?.data instanceof Blob) {
      try {
        const text = await (ax.response.data as Blob).text()
        ax.response.data = JSON.parse(text)
      } catch {
        /* body wasn't JSON; describe() will fall back to generic */
      }
    }
    ui.pushToast(describe(err), 'error')
  }
}

function openConfirm(kind: ConfirmKind, file: AdminFileItem) {
  confirm.value = { kind, file, reason: '', busy: false }
}

function closeConfirm() {
  confirm.value = null
}

async function submitConfirm() {
  const c = confirm.value
  if (c == null) return
  // Release still requires a justification — admin is reactivating
  // a file the AV scanner flagged. Purge does not — it's the cleanup
  // path for a row the admin has already reviewed.
  if (c.kind === 'release' && c.reason.trim().length < 10) return
  c.busy = true
  try {
    if (c.kind === 'release') {
      await adminQuarantineRelease(c.file.file_id, { reason: c.reason })
      ui.pushToast(t('admin_quarantine.toast.released'), 'success')
    } else {
      await adminQuarantinePurge(c.file.file_id)
      ui.pushToast(t('admin_quarantine.toast.purged'), 'success')
    }
    confirm.value = null
    await load()
  } catch (err) {
    c.busy = false
    ui.pushToast(describe(err), 'error')
  }
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <span class="fh-eyebrow">{{ t('admin_quarantine.eyebrow') }}</span>
      </div>
      <span class="fh-mono total-count">
        {{ t('admin_quarantine.total_count', { n: total }) }}
      </span>
    </div>

    <hr class="fh-rule" />

    <p class="fh-field-help intro">{{ t('admin_quarantine.intro') }}</p>

    <div class="filters">
      <input
        v-model.trim="q"
        type="search"
        class="fh-field-input search"
        :placeholder="t('admin_quarantine.search_placeholder')"
      />
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

    <table v-else-if="items.length > 0" class="files-table">
      <thead>
        <tr>
          <th>{{ t('admin_quarantine.col.filename') }}</th>
          <th class="numeric">{{ t('admin_quarantine.col.size') }}</th>
          <th>{{ t('admin_quarantine.col.uploader') }}</th>
          <th>{{ t('admin_quarantine.col.share') }}</th>
          <th>{{ t('admin_quarantine.col.uploaded') }}</th>
          <th>{{ t('admin_quarantine.col.actions') }}</th>
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
            <div class="row-name">{{ it.uploader.display_name }}</div>
            <div class="fh-mono row-hint">{{ it.uploader.email }} · {{ it.uploader.role }}</div>
          </td>
          <td>
            <div class="row-name">{{ it.share_subject || '—' }}</div>
          </td>
          <td class="fh-mono">{{ formatDate(it.uploaded_at) }}</td>
          <td class="actions-cell">
            <button type="button" class="fh-btn-text" @click="onDownload(it)">
              {{ t('admin_quarantine.btn.download') }}
            </button>
            <button type="button" class="fh-btn-text" @click="openConfirm('release', it)">
              {{ t('admin_quarantine.btn.release') }}
            </button>
            <button type="button" class="fh-btn-text danger" @click="openConfirm('purge', it)">
              {{ t('admin_quarantine.btn.purge') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <p v-else class="fh-field-help empty">{{ t('admin_quarantine.empty') }}</p>

    <Pager v-model:page="page" :total="total" :page-size="pageSize" />

    <div v-if="confirm" class="confirm-backdrop" @click.self="closeConfirm" @keydown.escape="closeConfirm">
      <div
        class="confirm-card"
        role="dialog"
        :aria-label="
          confirm.kind === 'release'
            ? t('admin_quarantine.confirm.release_title')
            : t('admin_quarantine.confirm.purge_title')
        "
      >
        <h2 class="confirm-h2">
          {{
            confirm.kind === 'release'
              ? t('admin_quarantine.confirm.release_title')
              : t('admin_quarantine.confirm.purge_title')
          }}
        </h2>
        <p class="fh-field-help">
          {{
            confirm.kind === 'release'
              ? t('admin_quarantine.confirm.release_help')
              : t('admin_quarantine.confirm.purge_help')
          }}
        </p>
        <p class="target fh-mono">{{ confirm.file.filename }}</p>
        <template v-if="confirm.kind === 'release'">
          <label class="fh-field-label" :for="`reason-${confirm.file.file_id}`">
            {{ t('admin_quarantine.confirm.reason_label') }}
          </label>
          <textarea
            :id="`reason-${confirm.file.file_id}`"
            v-model="confirm.reason"
            class="fh-field-input"
            rows="3"
            maxlength="500"
            :placeholder="t('admin_quarantine.confirm.reason_placeholder')"
          ></textarea>
        </template>
        <div class="confirm-actions">
          <button type="button" class="fh-btn-text" @click="closeConfirm">
            {{ t('admin_quarantine.confirm.cancel') }}
          </button>
          <button
            type="button"
            class="fh-btn"
            :class="{ danger: confirm.kind === 'purge' }"
            :disabled="
              confirm.busy ||
              (confirm.kind === 'release' && confirm.reason.trim().length < 10)
            "
            @click="submitConfirm"
          >
            {{
              confirm.kind === 'release'
                ? t('admin_quarantine.confirm.confirm_release')
                : t('admin_quarantine.confirm.confirm_purge')
            }}
          </button>
        </div>
      </div>
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
}

.search {
  flex: 1;
  max-width: 360px;
}

.loading,
.empty {
  color: var(--fh-subtle);
  padding: var(--fh-space-4) 0;
}

.files-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fh-text-body-sm);
}

.files-table th,
.files-table td {
  text-align: left;
  padding: var(--fh-space-2) var(--fh-space-3);
  border-bottom: var(--fh-border);
  vertical-align: top;
}

.files-table th {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: normal;
}

.numeric {
  text-align: right;
}

.row-name {
  color: var(--fh-ink);
}

.row-hint {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.actions-cell {
  display: flex;
  gap: var(--fh-space-2);
  flex-wrap: wrap;
}

.danger {
  color: var(--fh-danger, #b91c1c);
}


.confirm-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(20, 16, 8, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 60;
  padding: var(--fh-space-3);
}

.confirm-card {
  background: var(--fh-paper);
  padding: var(--fh-space-5);
  border-radius: var(--fh-radius-md);
  max-width: 540px;
  width: 100%;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-3);
}

.confirm-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  margin: 0;
}

.target {
  background: var(--fh-paper-raised);
  padding: var(--fh-space-2) var(--fh-space-3);
  border-radius: var(--fh-radius-sm);
  font-size: var(--fh-text-mono-sm);
  word-break: break-all;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--fh-space-3);
}
</style>
