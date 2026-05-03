<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { listUsers } from '@/api/admin'
import { inviteUser } from '@/api/account'
import { listGroups } from '@/api/groups'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type { AdminUserItem, GroupResponse, UserRole } from '@/types/api'

const router = useRouter()
const { t, locale } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const items = ref<AdminUserItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const q = ref('')
const role = ref<UserRole | ''>('')
const loading = ref(true)
const errorMsg = ref<string | null>(null)

let searchTimer: ReturnType<typeof setTimeout> | null = null

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await listUsers({
      q: q.value || undefined,
      role: role.value || undefined,
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

watch(q, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    void load()
  }, 220)
})
watch(role, () => {
  page.value = 1
  void load()
})
watch(page, load)

function open(u: AdminUserItem) {
  router.push({ name: 'admin-user-detail', params: { id: u.id } })
}

// --- Invite form ----------------------------------------------------------

const showInviteForm = ref(false)
const inviteEmail = ref('')
const inviteDisplayName = ref('')
const inviteRole = ref<UserRole>('client')
const inviting = ref(false)
const inviteError = ref<string | null>(null)
const availableGroups = ref<GroupResponse[]>([])
const selectedGroupIds = ref<number[]>([])

function resetInviteForm() {
  inviteEmail.value = ''
  inviteDisplayName.value = ''
  inviteRole.value = 'client'
  selectedGroupIds.value = []
  inviteError.value = null
}

function closeInviteForm() {
  showInviteForm.value = false
  resetInviteForm()
}

async function openInviteForm() {
  showInviteForm.value = true
  // Lazy-load groups the first time the form is opened.
  if (availableGroups.value.length === 0) {
    try {
      const { data } = await listGroups()
      availableGroups.value = data.items
    } catch {
      /* leave empty — checkbox section just won't render */
    }
  }
}

function toggleGroup(id: number) {
  const idx = selectedGroupIds.value.indexOf(id)
  if (idx === -1) {
    selectedGroupIds.value = [...selectedGroupIds.value, id]
  } else {
    selectedGroupIds.value = selectedGroupIds.value.filter((g) => g !== id)
  }
}

async function onInvite() {
  inviting.value = true
  inviteError.value = null
  try {
    const { data } = await inviteUser({
      email: inviteEmail.value,
      display_name_hint: inviteDisplayName.value,
      target_role: inviteRole.value,
      initial_group_ids: selectedGroupIds.value,
    })
    ui.pushToast(
      t('admin_users.invite_sent', {
        hint: data.email,
        expires: formatDate(data.expires_at),
      }),
      'success',
    )
    closeInviteForm()
  } catch (err) {
    inviteError.value = describe(err)
  } finally {
    inviting.value = false
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(
    locale.value === 'de' ? 'de-AT' : 'en-US',
    { year: 'numeric', month: 'short', day: '2-digit' },
  )
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <span class="fh-eyebrow">{{ t('admin_users.eyebrow') }}</span>
      </div>
      <button
        v-if="!showInviteForm"
        type="button"
        class="fh-btn"
        @click="openInviteForm"
      >
        {{ t('admin_users.invite_button') }} <span aria-hidden="true">→</span>
      </button>
    </div>

    <hr class="fh-rule" />

    <form v-if="showInviteForm" class="invite-form" @submit.prevent="onInvite">
      <h2 class="form-h2">{{ t('admin_users.invite_heading') }}</h2>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_users.invite_email_label') }}</span>
        <input
          v-model.trim="inviteEmail"
          class="fh-field-input fh-field-mono"
          type="email"
          autocomplete="off"
          required
        />
        <span class="fh-field-help">{{ t('admin_users.invite_email_help') }}</span>
      </label>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_users.invite_display_name_label') }}</span>
        <input
          v-model.trim="inviteDisplayName"
          class="fh-field-input"
          type="text"
          maxlength="120"
          required
        />
        <span class="fh-field-help">{{ t('admin_users.invite_display_name_help') }}</span>
      </label>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_users.invite_role_label') }}</span>
        <select v-model="inviteRole" class="fh-field-input">
          <option value="client">client</option>
          <option value="employee">employee</option>
          <option value="admin">admin</option>
        </select>
      </label>
      <div v-if="availableGroups.length > 0" class="fh-field">
        <span class="fh-field-label">{{ t('admin_users.invite_groups_label') }}</span>
        <span class="fh-field-help">{{ t('admin_users.invite_groups_help') }}</span>
        <ul class="group-checks">
          <li v-for="g in availableGroups" :key="g.id">
            <label class="group-check">
              <input
                type="checkbox"
                :checked="selectedGroupIds.includes(g.id)"
                @change="toggleGroup(g.id)"
              />
              <span class="group-name">{{ g.name }}</span>
              <span v-if="g.is_company_inbox" class="fh-pill">
                {{ t('admin_users.invite_groups_inbox') }}
              </span>
            </label>
          </li>
        </ul>
      </div>
      <div v-if="inviteError" class="fh-notice" data-tone="error">{{ inviteError }}</div>
      <div class="form-actions">
        <button
          type="submit"
          class="fh-btn"
          :disabled="inviting || !inviteEmail || !inviteDisplayName"
        >
          {{ inviting ? t('common.loading') : t('admin_users.invite_submit') }}
        </button>
        <button type="button" class="fh-btn-text" @click="closeInviteForm">
          {{ t('common.cancel') }}
        </button>
      </div>
    </form>

    <template v-if="!showInviteForm">
    <div class="filters">
      <input
        v-model.trim="q"
        type="search"
        class="fh-field-input search"
        :placeholder="t('admin_users.search_placeholder')"
      />
      <select v-model="role" class="role-select">
        <option value="">{{ t('admin_users.role_all') }}</option>
        <option value="admin">admin</option>
        <option value="employee">employee</option>
        <option value="client">client</option>
      </select>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

    <table v-else class="user-table">
      <thead>
        <tr>
          <th class="id-col">{{ t('admin_users.col.id') }}</th>
          <th>{{ t('admin_users.col.name') }}</th>
          <th>{{ t('admin_users.col.role') }}</th>
          <th>{{ t('admin_users.col.status') }}</th>
          <th>{{ t('admin_users.col.2fa') }}</th>
          <th>{{ t('admin_users.col.created') }}</th>
          <th>{{ t('admin_users.col.last_login') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="u in items"
          :key="u.id"
          tabindex="0"
          @click="open(u)"
          @keydown.enter="open(u)"
        >
          <td class="fh-mono id-col">{{ u.id }}</td>
          <td>
            <div class="row-name">{{ u.display_name }}</div>
            <div class="row-hint fh-mono">{{ u.email }}</div>
          </td>
          <td><span class="fh-mono role">{{ u.role }}</span></td>
          <td>
            <span v-if="u.is_disabled" class="fh-pill" data-state="danger">{{ t('admin_users.status.disabled') }}</span>
            <span v-else-if="u.requires_2fa" class="fh-pill" data-state="warn">{{ t('admin_users.status.needs_2fa') }}</span>
            <span v-else class="fh-pill" data-state="active">{{ t('admin_users.status.active') }}</span>
          </td>
          <td>
            <span v-if="u.has_2fa" class="fh-mono">on</span>
            <span v-else class="fh-mono subtle">off</span>
          </td>
          <td class="fh-mono">{{ formatDate(u.created_at) }}</td>
          <td class="fh-mono">{{ formatDate(u.last_login_at) }}</td>
        </tr>
      </tbody>
    </table>

    <div v-if="total > pageSize" class="pager">
      <button type="button" class="fh-btn-text" :disabled="page === 1" @click="page -= 1">
        ← {{ t('admin_users.prev') }}
      </button>
      <span class="fh-mono page-info">
        {{ t('admin_users.page_of', { page, total: Math.ceil(total / pageSize) }) }}
      </span>
      <button
        type="button"
        class="fh-btn-text"
        :disabled="page * pageSize >= total"
        @click="page += 1"
      >
        {{ t('admin_users.next') }} →
      </button>
    </div>
    </template>
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
  align-items: baseline;
}

.invite-form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  max-width: 520px;
  margin-bottom: var(--fh-space-4);
  padding-bottom: var(--fh-space-4);
  border-bottom: 1px solid var(--fh-hairline);
}

.form-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.25rem;
  margin: 0 0 var(--fh-space-2);
}

.form-actions {
  display: flex;
  gap: var(--fh-space-3);
  align-items: baseline;
  margin-top: var(--fh-space-2);
}

.group-checks {
  list-style: none;
  margin: var(--fh-space-1) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
}

.group-check {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-2);
  cursor: pointer;
}

.group-name {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
}

.search {
  flex: 1;
  max-width: 360px;
}

.role-select {
  font: inherit;
  background: transparent;
  border: var(--fh-border-strong);
  border-radius: var(--fh-radius-sm);
  padding: 4px 8px;
  color: var(--fh-ink);
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}

.user-table {
  width: 100%;
  border-collapse: collapse;
}

.user-table th {
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

.user-table tbody tr {
  cursor: pointer;
}

.user-table tbody tr:hover {
  background: var(--fh-paper-raised);
}

.user-table tbody tr:focus-visible {
  outline: 2px solid var(--fh-focus-ring);
  outline-offset: -2px;
}

.user-table td {
  padding: var(--fh-space-3) var(--fh-space-3) var(--fh-space-3) 0;
  border-bottom: var(--fh-border);
  vertical-align: middle;
}

.row-name {
  color: var(--fh-ink);
}

.row-hint {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.id-col {
  width: 4rem;
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.role {
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
}

.subtle {
  color: var(--fh-subtle);
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
