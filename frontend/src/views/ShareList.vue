<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useEscapeToClose } from '@/composables/useEscapeToClose'
import { useRoute, useRouter } from 'vue-router'

import { bulkExpireShares } from '@/api/shares'
import Pager from '@/components/Pager.vue'
import { useApiError } from '@/composables/useApiError'
import { useShareListState } from '@/composables/useShareListState'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import type { ShareListItem, ShareRecipientRef } from '@/types/api'
import { formatBytes } from '@/utils/bytes'
import { formatExpiryInSiteTime } from '@/utils/datetime'
import { shareStatePill } from '@/utils/statePill'

const { t, locale } = useI18n()
const { formatDate } = useSiteDateFormat()
const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const { describe } = useApiError()

const box = computed<'outbox' | 'inbox'>(() =>
  route.name === 'inbox' ? 'inbox' : 'outbox',
)

// All the filter + pagination + group-rendering state lives in
// `composables/useShareListState.ts` so this view stays focused on
// template + per-row navigation glue.
const {
  items,
  total,
  page,
  pageSize,
  loading,
  errorMsg,
  stateFilter,
  partyKind,
  partyGroup,
  userQuery,
  userSuggestions,
  myGroups,
  subjectQuery,
  groupBy,
  sort,
  groupedItems,
  groupByOptions,
  selectedCount,
  pickUser,
  clearAllFilters,
  pickGroup,
  load,
  isSelected,
  toggleSelected,
  clearSelection,
  setGroupSelection,
} = useShareListState(box)

const bulkConfirmOpen = ref(false)
const bulkInProgress = ref(false)

function openBulkConfirm() {
  if (selectedCount.value === 0) return
  bulkConfirmOpen.value = true
}
function closeBulkConfirm() {
  if (!bulkInProgress.value) bulkConfirmOpen.value = false
}

// See useEscapeToClose: the backdrop's own @keydown.escape never fires.
useEscapeToClose(computed(() => bulkConfirmOpen.value), closeBulkConfirm)

async function confirmBulkExpire() {
  const ids = Array.from(
    items.value.filter((i) => isSelected(i.id)).map((i) => i.id),
  )
  if (ids.length === 0) {
    bulkConfirmOpen.value = false
    return
  }
  bulkInProgress.value = true
  try {
    const { data } = await bulkExpireShares(ids)
    const expiredN = data.expired.length
    const failedN = data.failed.length
    if (expiredN > 0 && failedN === 0) {
      ui.pushToast(
        t('share_list.bulk.toast.all_expired', { n: expiredN }),
        'success',
      )
    } else if (expiredN > 0 && failedN > 0) {
      ui.pushToast(
        t('share_list.bulk.toast.partial', { ok: expiredN, fail: failedN }),
        'success',
      )
    } else {
      ui.pushToast(t('share_list.bulk.toast.all_failed'), 'error')
    }
    bulkConfirmOpen.value = false
    clearSelection()
    void load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    bulkInProgress.value = false
  }
}


function formatExpiry(iso: string | null): string {
  // Full date + HH:MM + tz (formatInSiteTime defaults) - a bare zone token
  // with no clock ("Jun 08, 2026, GMT+2") is meaningless.
  return formatExpiryInSiteTime(iso, locale.value, t('expiry.never_label'))
}

function open(s: ShareListItem) {
  router.push({ name: 'share-detail', params: { id: s.id } })
}

// Compact recipient list for the outbox column. Shows the first
// two labels; collapses the rest as "+N" so multi-recipient shares
// don't blow up the row width.
function recipientLabel(r: ShareRecipientRef): string {
  // Inbound submissions carry a synthetic "company" recipient - translate it.
  return r.kind === 'company' ? t('share_list.company') : r.label
}

function recipientSummary(rs: ShareRecipientRef[]): string {
  if (!rs || rs.length === 0) return '-'
  if (rs.length <= 2) return rs.map(recipientLabel).join(', ')
  return `${recipientLabel(rs[0])}, ${recipientLabel(rs[1])} +${rs.length - 2}`
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <span class="fh-eyebrow">{{ t(`share_list.eyebrow.${box}`) }}</span>
        <h1 class="fh-display-md">{{ t(`share_list.title.${box}`) }}</h1>
      </div>
      <RouterLink
        v-if="box === 'outbox'"
        :to="{ name: 'share-create' }"
        class="fh-btn"
      >
        {{ t('share_list.new_share') }} <span aria-hidden="true">→</span>
      </RouterLink>
    </div>

    <hr class="fh-rule" />

    <div class="filters">
      <input
        v-model="subjectQuery"
        type="search"
        class="fh-field-input subject-search"
        autocomplete="off"
        :aria-label="t('share_list.filter.subject_placeholder')"
        :placeholder="t('share_list.filter.subject_placeholder')"
      />

      <select
        v-model="stateFilter"
        class="filter-select"
        :aria-label="t('share_list.filter.state_all')"
      >
        <option value="">{{ t('share_list.filter.state_all') }}</option>
        <option value="pending_approval">{{ t('share_state.pending_approval') }}</option>
        <option value="active">{{ t('share_state.active') }}</option>
        <option value="rejected">{{ t('share_state.rejected') }}</option>
        <option value="expired">{{ t('share_state.expired') }}</option>
        <option value="revoked">{{ t('share_state.revoked') }}</option>
        <option value="deleted">{{ t('share_state.deleted') }}</option>
        <option value="failed">{{ t('share_state.failed') }}</option>
      </select>

      <select
        v-model="partyKind"
        class="filter-select"
        :aria-label="t(`share_list.filter.party_any.${box}`)"
      >
        <option value="any">
          {{ t(`share_list.filter.party_any.${box}`) }}
        </option>
        <option value="user">
          {{ t(`share_list.filter.party_user.${box}`) }}
        </option>
        <option value="group">
          {{ t(`share_list.filter.party_group.${box}`) }}
        </option>
      </select>

      <div v-if="partyKind === 'user'" class="party-picker">
        <input
          v-model.trim="userQuery"
          type="search"
          class="fh-field-input"
          autocomplete="off"
          :aria-label="t('share_list.filter.user_placeholder')"
          :placeholder="t('share_list.filter.user_placeholder')"
        />
        <ul v-if="userSuggestions.length > 0" class="suggestions">
          <li v-for="u in userSuggestions" :key="u.user_id">
            <button type="button" class="suggest-btn" @click="pickUser(u)">
              <span class="row-name">{{ u.display_name }}</span>
              <span class="fh-mono row-hint">{{ u.email }} · {{ u.role }}</span>
            </button>
          </li>
        </ul>
      </div>

      <select
        v-else-if="partyKind === 'group'"
        v-model="partyGroup"
        :aria-label="t('common.filter')"
        class="filter-select"
        @change="pickGroup"
      >
        <option :value="null">{{ t('share_list.filter.group_pick') }}</option>
        <option v-for="g in myGroups" :key="g.id" :value="g">
          {{ g.name }}
        </option>
      </select>

      <button
        v-if="partyKind !== 'any' || stateFilter || subjectQuery"
        type="button"
        class="fh-btn-text"
        @click="clearAllFilters"
      >
        {{ t('share_list.filter.clear') }}
      </button>

      <span class="spacer" />

      <label class="group-toggle">
        <span class="fh-mono toggle-label">{{ t('share_list.group.label') }}</span>
        <select v-model="groupBy" class="filter-select">
          <option v-for="opt in groupByOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
      </label>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div
v-else-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="items.length > 0">
      <div v-for="g in groupedItems" :key="g.key" class="group-section">
        <h2 v-if="groupBy !== 'none'" class="group-header">
          {{ g.label }}
          <span class="group-count fh-mono">· {{ g.items.length }}</span>
        </h2>
        <table class="share-table">
          <thead>
            <tr>
              <th v-if="box === 'outbox'" class="select-col">
                <input
                  type="checkbox"
                  :aria-label="t('share_list.bulk.select_all_aria')"
                  :checked="g.items.some((i) => i.state === 'active') && g.items.filter((i) => i.state === 'active').every((i) => isSelected(i.id))"
                  @change="(e) => setGroupSelection(g.items.filter((i) => i.state === 'active').map((i) => i.id), (e.target as HTMLInputElement).checked)"
                  @click.stop
                />
              </th>
              <th
                role="button"
                tabindex="0"
                :aria-sort="sort.ariaSort('subject')"
                @click="sort.toggle('subject')"
                @keydown.enter="sort.toggle('subject')"
              >
                {{ t('share_list.col.subject') }}
                <span class="sort-ind">{{ sort.indicator('subject') }}</span>
              </th>
              <th
                v-if="box === 'inbox'"
                role="button"
                tabindex="0"
                :aria-sort="sort.ariaSort('created_at')"
                @click="sort.toggle('created_at')"
                @keydown.enter="sort.toggle('created_at')"
              >
                {{ t('share_list.col.sender') }}
                <span class="sort-ind">{{ sort.indicator('created_at') }}</span>
              </th>
              <th v-if="box === 'outbox'">
                {{ t('share_list.col.recipients') }}
              </th>
              <th>{{ t('share_list.col.kind') }}</th>
              <th
                role="button"
                tabindex="0"
                :aria-sort="sort.ariaSort('state')"
                @click="sort.toggle('state')"
                @keydown.enter="sort.toggle('state')"
              >
                {{ t('share_list.col.state') }}
                <span class="sort-ind">{{ sort.indicator('state') }}</span>
              </th>
              <th>{{ t('share_list.col.files') }}</th>
              <th>{{ t('share_list.col.size') }}</th>
              <th
                role="button"
                tabindex="0"
                :aria-sort="sort.ariaSort('expires_at')"
                @click="sort.toggle('expires_at')"
                @keydown.enter="sort.toggle('expires_at')"
              >
                {{ t('share_list.col.expires') }}
                <span class="sort-ind">{{ sort.indicator('expires_at') }}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in g.items"
              :key="`${g.key}-${item.id}`"
              tabindex="0"
              @click="open(item)"
              @keydown.enter="open(item)"
            >
              <td v-if="box === 'outbox'" class="select-col" @click.stop>
                <input
                  v-if="item.state === 'active'"
                  type="checkbox"
                  :checked="isSelected(item.id)"
                  :aria-label="t('share_list.bulk.select_row_aria')"
                  @change="toggleSelected(item.id)"
                />
              </td>
              <td class="subject-cell">
                <div class="subject">
                  {{ item.effective_subject || t('share_list.no_subject') }}
                </div>
                <div class="created fh-mono">
                  {{ t('share_list.created', { d: formatDate(item.created_at) }) }}
                </div>
              </td>
              <td v-if="box === 'inbox'">
                <span v-if="item.sender" class="row-name">{{ item.sender.display_name }}</span>
                <span v-else class="fh-mono row-hint">-</span>
              </td>
              <td v-if="box === 'outbox'" class="recipients-cell">
                {{ recipientSummary(item.recipients) }}
              </td>
              <td>
                <span class="fh-mono kind">{{ t(`share_kind.${item.kind}`) }}</span>
              </td>
              <td>
                <span class="fh-pill" :data-state="shareStatePill(item.state)">
                  {{ t(`share_state.${item.state}`) }}
                </span>
              </td>
              <td class="numeric">{{ item.file_count }}</td>
              <td class="numeric fh-mono">{{ formatBytes(item.total_size_bytes) }}</td>
              <td class="fh-mono">{{ formatExpiry(item.expires_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <Pager v-model:page="page" :total="total" :page-size="pageSize" />
    </template>

    <div v-else class="empty-state">
      <p class="fh-display-md empty-display">{{ t(`share_list.empty.${box}.title`) }}</p>
      <p class="fh-field-help">{{ t(`share_list.empty.${box}.subtitle`) }}</p>
      <RouterLink
        v-if="box === 'outbox'"
        :to="{ name: 'share-create' }"
        class="fh-btn"
      >
        {{ t('share_list.new_share') }} <span aria-hidden="true">→</span>
      </RouterLink>
    </div>

    <Transition name="bulk-bar">
      <div v-if="box === 'outbox' && selectedCount > 0" class="bulk-bar" role="region" :aria-label="t('share_list.bulk.bar_aria')">
        <span class="bulk-count fh-mono">
          {{ t('share_list.bulk.selected', { n: selectedCount }) }}
        </span>
        <button type="button" class="fh-btn fh-btn--danger" @click="openBulkConfirm">
          {{ t('share_list.bulk.action') }}
        </button>
        <button type="button" class="fh-btn-text" @click="clearSelection">
          {{ t('share_list.bulk.clear') }}
        </button>
      </div>
    </Transition>

    <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- modal backdrop: click-outside is a convenience, Escape is the keyboard path; revisited with the modal focus work -->
    <div
      v-if="bulkConfirmOpen"
      class="fh-modal-backdrop"
      @click.self="closeBulkConfirm"
      @keydown.escape="closeBulkConfirm"
    >
      <div class="fh-modal fh-modal--small" role="dialog" :aria-label="t('share_list.bulk.confirm.title')">
        <h2 class="modal-h2">{{ t('share_list.bulk.confirm.title') }}</h2>
        <p class="modal-body">
          {{ t('share_list.bulk.confirm.body', { n: selectedCount }) }}
        </p>
        <div class="form-actions">
          <button
            type="button"
            class="fh-btn fh-btn--danger"
            :disabled="bulkInProgress"
            @click="confirmBulkExpire"
          >
            {{ bulkInProgress ? t('common.loading') : t('share_list.bulk.confirm.action') }}
          </button>
          <button
            type="button"
            class="fh-btn-text"
            :disabled="bulkInProgress"
            @click="closeBulkConfirm"
          >
            {{ t('common.cancel') }}
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

.filters {
  display: flex;
  gap: var(--fh-space-3);
  margin-bottom: var(--fh-space-4);
  align-items: center;
  flex-wrap: wrap;
}

.spacer {
  flex: 1;
}

.filter-select {
  font: inherit;
  background: transparent;
  border: var(--fh-border-strong);
  border-radius: var(--fh-radius-sm);
  padding: 4px 8px;
  color: var(--fh-ink);
}

.subject-search {
  flex: 1 1 220px;
  min-width: 180px;
  max-width: 320px;
}

.party-picker {
  position: relative;
  flex: 1;
  max-width: 280px;
}

.suggestions {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--fh-hairline);
  background: var(--fh-paper-raised);
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  z-index: 5;
  max-height: 220px;
  overflow-y: auto;
}

.suggest-btn {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--fh-space-2);
  width: 100%;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
  font: inherit;
}

.suggest-btn:hover {
  background: var(--fh-paper-sunk);
}

.row-name {
  font-weight: 500;
}

.row-hint {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.group-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-2);
}

.toggle-label {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.group-section + .group-section {
  margin-top: var(--fh-space-4);
}

.group-header {
  font-family: var(--fh-font-display);
  font-size: 1.15rem;
  margin: 0 0 var(--fh-space-2);
}

.group-count {
  color: var(--fh-subtle);
  font-size: var(--fh-text-mono-sm);
  margin-left: var(--fh-space-1);
}

.share-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: var(--fh-space-3);
}

.share-table th,
.share-table td {
  text-align: left;
  padding: var(--fh-space-2) var(--fh-space-3);
  border-bottom: 1px solid var(--fh-rule);
  vertical-align: top;
}

.share-table th {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--fh-subtle);
  font-weight: 500;
  user-select: none;
}

.share-table th[role="button"] {
  cursor: pointer;
}

.share-table th[role="button"]:hover {
  color: var(--fh-ink);
}

.sort-ind {
  display: inline-block;
  width: 1ch;
  margin-left: 2px;
  color: var(--fh-accent);
}

.share-table tbody tr {
  cursor: pointer;
  transition: background 120ms;
}

.share-table tbody tr:hover {
  background: var(--fh-hover);
}

/* These rows are `tabindex="0"` and Enter navigates, so they need a real
   indicator. They used to set `outline: none` and rely on a background from an
   undefined custom property: focus moved through the table invisibly and Enter
   opened whichever row happened to have it (audit #2). Inset, because an
   outset ring on a table row is clipped by the neighbouring cells. */
.share-table tbody tr:focus-visible {
  background: var(--fh-hover);
  outline: 2px solid var(--fh-focus-ring);
  outline-offset: -2px;
}

.subject {
  font-weight: 500;
}

.recipients-cell {
  max-width: 18rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.created {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.numeric {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}

.empty-state {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  align-items: flex-start;
  padding: var(--fh-space-5) 0;
}

.empty-display {
  margin: 0;
}

.select-col {
  width: 2rem;
  text-align: center;
}

.select-col input[type="checkbox"] {
  cursor: pointer;
  width: 16px;
  height: 16px;
  accent-color: var(--fh-accent);
}

.bulk-bar {
  position: fixed;
  left: 50%;
  bottom: var(--fh-space-4);
  transform: translateX(-50%);
  background: var(--fh-paper);
  border: 1px solid var(--fh-hairline-strong);
  box-shadow: 0 8px 32px rgba(26, 29, 36, 0.15);
  padding: var(--fh-space-2) var(--fh-space-4);
  display: flex;
  align-items: center;
  gap: var(--fh-space-3);
  z-index: 50;
  border-radius: var(--fh-radius-sm);
}

.bulk-count {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.bulk-bar-enter-active,
.bulk-bar-leave-active {
  transition:
    opacity 180ms cubic-bezier(0.2, 0, 0, 1),
    transform 200ms cubic-bezier(0.2, 0, 0, 1);
}

.bulk-bar-enter-from,
.bulk-bar-leave-to {
  opacity: 0;
  transform: translate(-50%, 12px);
}

.fh-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(26, 29, 36, 0.4);
  display: grid;
  place-items: center;
  z-index: 100;
}

.fh-modal {
  background: var(--fh-paper);
  border: 1px solid var(--fh-hairline-strong);
  box-shadow: 0 8px 40px rgba(26, 29, 36, 0.15);
  padding: var(--fh-space-5);
  width: min(560px, 92vw);
  max-height: 92vh;
  overflow-y: auto;
}

.fh-modal--small {
  width: min(420px, 92vw);
}

.modal-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.25rem;
  margin: 0 0 var(--fh-space-3);
}

.modal-body {
  margin: 0 0 var(--fh-space-4);
  color: var(--fh-ink);
}

.form-actions {
  display: flex;
  gap: var(--fh-space-3);
  align-items: baseline;
  margin-top: var(--fh-space-2);
}
</style>
