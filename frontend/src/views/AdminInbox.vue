<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

import { fetchInboxNow, listInbox } from '@/api/admin'
import Pager from '@/components/Pager.vue'
import { useApiError } from '@/composables/useApiError'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import type { InboxClass, InboxListItem } from '@/types/api'

const { t } = useI18n()
const { describe } = useApiError()
const { formatDate } = useSiteDateFormat()
const ui = useUiStore()
const router = useRouter()

const loading = ref(true)
const fetching = ref(false)
const errorMsg = ref<string | null>(null)
const items = ref<InboxListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50

const q = ref('')
const classification = ref('')
const status = ref('')

const classTone: Record<InboxClass, string> = {
  normal: 'reply',
  bounce: 'bounce',
  auto_reply: 'auto',
}

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
    const { data } = await listInbox({
      q: q.value || undefined,
      classification: classification.value || undefined,
      status: status.value || undefined,
      page: page.value,
      page_size: pageSize,
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

function applyFilters() {
  page.value = 1
  load()
}

function open(id: number) {
  router.push({ name: 'admin-inbox-detail', params: { id } })
}

async function onFetchNow() {
  fetching.value = true
  try {
    const { data } = await fetchInboxNow()
    if (data.ok && data.skipped) {
      ui.pushToast(t('admin_imap.fetch_skipped', { reason: data.skipped }), 'warn')
    } else if (data.ok && (data.ingested ?? 0) > 0) {
      ui.pushToast(t('admin_inbox.fetch_done', { n: data.ingested ?? 0 }), 'success')
    } else if (data.ok) {
      ui.pushToast(
        t('admin_inbox.fetch_empty', {
          mailbox: data.mailbox ?? 'INBOX',
          total: data.total ?? 0,
        }),
        'success',
      )
    } else {
      ui.pushToast(data.error || t('admin_imap.fetch_failed'), 'warn')
    }
    page.value = 1
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'warn')
  } finally {
    fetching.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="inbox-page" data-density="operator">
    <div class="page-head">
      <div>
        <span class="fh-eyebrow">{{ t('admin_inbox.eyebrow') }}</span>
        <h1 class="page-title">{{ t('admin_inbox.title') }}</h1>
      </div>
      <button type="button" class="fh-btn" :disabled="fetching" @click="onFetchNow">
        {{ t('admin_inbox.fetch_now') }}
      </button>
    </div>
    <p class="fh-field-help intro">{{ t('admin_inbox.intro') }}</p>

    <div class="filters">
      <input
        v-model.trim="q"
        type="search"
        class="fh-field-input"
        :placeholder="t('admin_inbox.search_placeholder')"
        @keyup.enter="applyFilters"
      />
      <select v-model="classification" class="fh-field-input" @change="applyFilters">
        <option value="">{{ t('admin_inbox.class_all') }}</option>
        <option value="normal">{{ t('admin_inbox.class_normal') }}</option>
        <option value="bounce">{{ t('admin_inbox.class_bounce') }}</option>
        <option value="auto_reply">{{ t('admin_inbox.class_auto') }}</option>
      </select>
      <select v-model="status" class="fh-field-input" @change="applyFilters">
        <option value="">{{ t('admin_inbox.status_all') }}</option>
        <option value="new">{{ t('admin_inbox.status_new') }}</option>
        <option value="read">{{ t('admin_inbox.status_read') }}</option>
        <option value="archived">{{ t('admin_inbox.status_archived') }}</option>
      </select>
    </div>

    <div v-if="errorMsg" class="fh-notice" data-tone="danger">{{ errorMsg }}</div>
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <p v-else-if="!items.length" class="empty">{{ t('admin_inbox.empty') }}</p>

    <table v-else class="fh-table">
      <thead>
        <tr>
          <th>{{ t('admin_inbox.col_type') }}</th>
          <th>{{ t('admin_inbox.col_from') }}</th>
          <th>{{ t('admin_inbox.col_subject') }}</th>
          <th>{{ t('admin_inbox.col_received') }}</th>
        </tr>
      </thead>
      <tbody>
        <!-- A row whose only affordance is @click is unreachable without a
             mouse: no tab stop, no key handler, and nothing telling a screen
             reader it does anything. tabindex + role + the two activation keys
             are the minimum that makes it a control (audit 2026-07-30). -->
        <tr
          v-for="m in items"
          :key="m.id"
          class="row"
          :class="{ unread: m.status === 'new' }"
          tabindex="0"
          role="button"
          :aria-label="t('admin_inbox.open_message', { subject: m.subject || m.sender_email })"
          @click="open(m.id)"
          @keydown.enter.prevent="open(m.id)"
          @keydown.space.prevent="open(m.id)"
        >
          <td><span class="badge" :data-tone="classTone[m.classification]">{{ t(`admin_inbox.tag_${m.classification}`) }}</span></td>
          <td>
            <span class="from">{{ m.sender_name || m.sender_email }}</span>
            <span v-if="m.sender_name" class="fh-mono addr">{{ m.sender_email }}</span>
          </td>
          <td>
            {{ m.subject }}
            <span v-if="m.has_attachments" class="clip" :title="t('admin_inbox.has_attachments')">📎</span>
          </td>
          <td class="fh-mono">{{ formatDate(m.received_at || m.created_at) }}</td>
        </tr>
      </tbody>
    </table>

    <Pager v-if="!loading && total > pageSize" :page="page" :total="total" :page-size="pageSize" @update:page="(n) => { page = n; load() }" />
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--fh-space-3);
}
.page-title {
  font-family: var(--fh-font-display);
  font-weight: normal;
  font-size: var(--fh-text-display-md);
  margin: var(--fh-space-1) 0;
}
.intro {
  margin-bottom: var(--fh-space-4);
}
.filters {
  display: flex;
  gap: var(--fh-space-2);
  margin-bottom: var(--fh-space-3);
}
.filters .fh-field-input {
  width: auto;
}
.row {
  cursor: pointer;
}
.row:hover {
  background: var(--fh-paper-sunk);
}
.row.unread {
  font-weight: 600;
}
.badge {
  font-size: var(--fh-text-mono-sm);
  font-family: var(--fh-font-mono);
  padding: 0.1rem 0.4rem;
  border-radius: var(--fh-radius-sm);
  border: 1px solid var(--fh-hairline);
}
.badge[data-tone='bounce'] {
  color: var(--fh-danger);
  border-color: var(--fh-danger);
}
.badge[data-tone='auto'] {
  color: var(--fh-warning);
  border-color: var(--fh-warning);
}
.addr {
  display: block;
  color: var(--fh-ink-soft);
  font-size: var(--fh-text-mono-sm);
}
.empty {
  color: var(--fh-ink-soft);
  padding: var(--fh-space-4) 0;
}
</style>
