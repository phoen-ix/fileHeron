<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { adminListSessions, adminRevokeSession, adminRevokeUserSessions } from '@/api/admin'
import Pager from '@/components/Pager.vue'
import { useApiError } from '@/composables/useApiError'
import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
import { usePaginatedList } from '@/composables/usePaginatedList'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useTableSort } from '@/composables/useTableSort'
import { useUiStore } from '@/stores/ui'
import type { AdminSessionRow } from '@/types/api'
import { uaShort } from '@/utils/ua'

const { t } = useI18n()
const { formatDate } = useSiteDateFormat()
const { describe } = useApiError()
const ui = useUiStore()

const q = ref('')
const includeInactive = ref(false)
const revokingId = ref<number | null>(null)
const revokingUserId = ref<number | null>(null)

const sort = useTableSort({ defaultBy: 'last_used_at', defaultDir: 'asc' })

const { items, total, page, pageSize, loading, errorMsg, load } =
  usePaginatedList<AdminSessionRow>(({ page, pageSize }) =>
    adminListSessions({
      q: q.value || undefined,
      include_inactive: includeInactive.value || undefined,
      sort: sort.sortBy.value as 'created_at' | 'last_used_at' | 'expires_at',
      direction: sort.sortDir.value,
      page,
      page_size: pageSize,
    }).then((r) => r.data),
  )

useDebouncedSearch(q, () => {
  page.value = 1
  void load()
})
watch(includeInactive, () => {
  page.value = 1
  void load()
})
watch([sort.sortBy, sort.sortDir, page], load)

async function onRevoke(s: AdminSessionRow) {
  if (revokingId.value) return
  const who = s.user_display_name || s.user_email || `#${s.user_id}`
  if (!(await ui.confirm({ message: t('admin_sessions.revoke_confirm', { who }), danger: true }))) return
  revokingId.value = s.id
  try {
    await adminRevokeSession(s.id)
    ui.pushToast(t('admin_sessions.revoked_toast'), 'success')
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    revokingId.value = null
  }
}

async function onRevokeAll(s: AdminSessionRow) {
  if (revokingUserId.value) return
  const who = s.user_display_name || s.user_email || `#${s.user_id}`
  if (!(await ui.confirm({ message: t('admin_sessions.revoke_all_confirm', { who }), danger: true }))) return
  revokingUserId.value = s.user_id
  try {
    const { data } = await adminRevokeUserSessions(s.user_id)
    ui.pushToast(t('admin_sessions.revoked_all_toast', { n: data.revoked }), 'success')
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    revokingUserId.value = null
  }
}


onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <span class="fh-eyebrow">{{ t('admin_sessions.eyebrow') }}</span>
      </div>
      <span class="fh-mono total-count">{{ t('admin_sessions.total_count', { n: total }) }}</span>
    </div>

    <hr class="fh-rule" />

    <p class="fh-field-help intro">{{ t('admin_sessions.intro') }}</p>

    <div class="filters">
      <input
        v-model.trim="q"
        type="search"
        class="fh-field-input search"
        :placeholder="t('admin_sessions.search_placeholder')"
      />
      <label class="toggle">
        <input v-model="includeInactive" type="checkbox" />
        {{ t('admin_sessions.include_inactive') }}
      </label>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

    <table v-else-if="items.length > 0" class="sessions-table">
      <thead>
        <tr>
          <th>{{ t('admin_sessions.col.user') }}</th>
          <th>{{ t('admin_sessions.col.device') }}</th>
          <th>{{ t('admin_sessions.col.ip') }}</th>
          <th
            role="button"
            tabindex="0"
            :aria-sort="sort.ariaSort('created_at')"
            @click="sort.toggle('created_at')"
            @keydown.enter="sort.toggle('created_at')"
          >
            {{ t('admin_sessions.col.started') }}
            <span class="sort-ind">{{ sort.indicator('created_at') }}</span>
          </th>
          <th
            role="button"
            tabindex="0"
            :aria-sort="sort.ariaSort('last_used_at')"
            @click="sort.toggle('last_used_at')"
            @keydown.enter="sort.toggle('last_used_at')"
          >
            {{ t('admin_sessions.col.last_active') }}
            <span class="sort-ind">{{ sort.indicator('last_used_at') }}</span>
          </th>
          <th
            role="button"
            tabindex="0"
            :aria-sort="sort.ariaSort('expires_at')"
            @click="sort.toggle('expires_at')"
            @keydown.enter="sort.toggle('expires_at')"
          >
            {{ t('admin_sessions.col.expires') }}
            <span class="sort-ind">{{ sort.indicator('expires_at') }}</span>
          </th>
          <th>{{ t('admin_sessions.col.actions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in items" :key="s.id" :class="{ inactive: !s.is_active }">
          <td>
            <RouterLink
              class="row-name user-link"
              :to="{ name: 'admin-user-detail', params: { id: s.user_id } }"
            >
              {{ s.user_display_name || `#${s.user_id}` }}
            </RouterLink>
            <div class="fh-mono row-hint">{{ s.user_email || t('admin_sessions.deleted_user') }}</div>
          </td>
          <td>
            {{ uaShort(s.created_ua, t('admin_sessions.unknown_device')) }}
            <span v-if="!s.is_active" class="fh-pill" data-state="danger">
              {{ s.revoked_at ? t('admin_sessions.state_revoked') : t('admin_sessions.state_expired') }}
            </span>
          </td>
          <td class="fh-mono">{{ s.created_ip || '-' }}</td>
          <td class="fh-mono">{{ formatDate(s.created_at) }}</td>
          <td class="fh-mono">{{ formatDate(s.last_used_at) }}</td>
          <td class="fh-mono">{{ formatDate(s.expires_at) }}</td>
          <td class="actions">
            <button
              v-if="s.is_active"
              type="button"
              class="fh-btn-text revoke-btn"
              :disabled="revokingId === s.id"
              @click="onRevoke(s)"
            >
              {{ revokingId === s.id ? t('common.loading') : t('admin_sessions.revoke') }}
            </button>
            <button
              type="button"
              class="fh-btn-text revoke-btn"
              :disabled="revokingUserId === s.user_id"
              @click="onRevokeAll(s)"
            >
              {{ t('admin_sessions.revoke_all') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <p v-else class="fh-field-help empty">{{ t('admin_sessions.empty') }}</p>

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

.toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-2);
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
  cursor: pointer;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}

.sessions-table {
  width: 100%;
  border-collapse: collapse;
}

.sessions-table th,
.sessions-table td {
  text-align: left;
  padding: var(--fh-space-2) var(--fh-space-3);
  border-bottom: 1px solid var(--fh-rule);
  vertical-align: top;
}

.sessions-table th {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--fh-subtle);
  font-weight: 500;
  user-select: none;
}

.sessions-table th[role="button"] {
  cursor: pointer;
}

.sessions-table th[role="button"]:hover {
  color: var(--fh-ink);
}

.sort-ind {
  display: inline-block;
  width: 1ch;
  margin-left: 2px;
  color: var(--fh-accent);
}

tr.inactive {
  opacity: 0.55;
}

.row-name {
  font-weight: 500;
}

.user-link {
  color: var(--fh-ink);
  text-decoration: none;
}

.user-link:hover {
  color: var(--fh-accent);
  text-decoration: underline;
}

.row-hint {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
  white-space: nowrap;
}

.revoke-btn {
  color: var(--fh-accent);
}

.empty {
  margin: var(--fh-space-3) 0;
}

</style>
