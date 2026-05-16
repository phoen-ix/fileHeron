<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { useShareListState } from '@/composables/useShareListState'
import type { ShareListItem, ShareRecipientRef, UserSearchItem } from '@/types/api'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()

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
  partyUser,
  partyGroup,
  userQuery,
  userSuggestions,
  myGroups,
  groupBy,
  sort,
  groupedItems,
  groupByOptions,
  pickUser,
  clearParty,
  clearAllFilters,
  pickGroup,
  load,
} = useShareListState(box)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

function pillForState(state: ShareState): string | undefined {
  if (state === 'active') return 'active'
  if (state === 'expired') return 'warn'
  if (state === 'revoked' || state === 'deleted') return 'danger'
  return undefined
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(
    locale.value === 'de' ? 'de-AT' : 'en-US',
    { year: 'numeric', month: 'short', day: '2-digit' },
  )
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const k = n / 1024
  if (k < 1024) return `${k.toFixed(1)} KB`
  const m = k / 1024
  if (m < 1024) return `${m.toFixed(1)} MB`
  return `${(m / 1024).toFixed(2)} GB`
}

function open(s: ShareListItem) {
  router.push({ name: 'share-detail', params: { id: s.id } })
}

// Compact recipient list for the outbox column. Shows the first
// two labels; collapses the rest as "+N" so multi-recipient shares
// don't blow up the row width.
function recipientSummary(rs: ShareRecipientRef[]): string {
  if (!rs || rs.length === 0) return '—'
  if (rs.length <= 2) return rs.map((r) => r.label).join(', ')
  return `${rs[0].label}, ${rs[1].label} +${rs.length - 2}`
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
      <select v-model="stateFilter" class="filter-select">
        <option value="">{{ t('share_list.filter.state_all') }}</option>
        <option value="active">{{ t('share_state.active') }}</option>
        <option value="expired">{{ t('share_state.expired') }}</option>
        <option value="revoked">{{ t('share_state.revoked') }}</option>
        <option value="deleted">{{ t('share_state.deleted') }}</option>
      </select>

      <select v-model="partyKind" class="filter-select">
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
        class="filter-select"
        @change="pickGroup"
      >
        <option :value="null">{{ t('share_list.filter.group_pick') }}</option>
        <option v-for="g in myGroups" :key="g.id" :value="g">
          {{ g.name }}
        </option>
      </select>

      <button
        v-if="partyKind !== 'any' || stateFilter"
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
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="items.length > 0">
      <div v-for="g in groupedItems" :key="g.key" class="group-section">
        <h2 v-if="groupBy !== 'none'" class="group-header">
          {{ g.label }}
          <span class="group-count fh-mono">· {{ g.items.length }}</span>
        </h2>
        <table class="share-table">
          <thead>
            <tr>
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
                <span v-else class="fh-mono row-hint">—</span>
              </td>
              <td v-if="box === 'outbox'" class="recipients-cell">
                {{ recipientSummary(item.recipients) }}
              </td>
              <td>
                <span class="fh-mono kind">{{ t(`share_kind.${item.kind}`) }}</span>
              </td>
              <td>
                <span class="fh-pill" :data-state="pillForState(item.state)">
                  {{ t(`share_state.${item.state}`) }}
                </span>
              </td>
              <td class="numeric">{{ item.file_count }}</td>
              <td class="numeric fh-mono">{{ formatBytes(item.total_size_bytes) }}</td>
              <td class="fh-mono">{{ formatDate(item.expires_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="totalPages > 1" class="pager">
        <button type="button" class="fh-btn-text" :disabled="page === 1" @click="page -= 1">
          ← {{ t('admin_users.prev') }}
        </button>
        <span class="fh-mono page-info">
          {{ t('admin_users.page_of', { page, total: totalPages }) }}
        </span>
        <button
          type="button"
          class="fh-btn-text"
          :disabled="page >= totalPages"
          @click="page += 1"
        >
          {{ t('admin_users.next') }} →
        </button>
      </div>
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

.share-table tbody tr:hover,
.share-table tbody tr:focus-visible {
  background: var(--fh-hover);
  outline: none;
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

.pager {
  display: flex;
  gap: var(--fh-space-3);
  align-items: baseline;
  justify-content: center;
  margin-top: var(--fh-space-4);
}
</style>
