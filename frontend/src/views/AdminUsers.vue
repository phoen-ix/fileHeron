<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import {
  activateInvite,
  listInvites,
  listUsers,
  regenerateInvite,
  resendInvite,
  revokeInvite,
} from '@/api/admin'
import PasswordStrength from '@/components/PasswordStrength.vue'
import { useApiError } from '@/composables/useApiError'
import { useInviteForm } from '@/composables/useInviteForm'
import { useUiStore } from '@/stores/ui'
import { formatBytes } from '@/utils/bytes'
import { formatDateInSiteTime } from '@/utils/datetime'
import type {
  AdminInviteItem,
  AdminUserItem,
  UserRole,
} from '@/types/api'

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

// --- Invite form (state + submit lifecycle in useInviteForm) -------------

const {
  showInviteForm,
  inviteEmail,
  inviteDisplayName,
  inviteRole,
  inviting,
  inviteError,
  availableGroups,
  selectedGroupIds,
  createDirectly,
  invitePassword,
  openInviteForm,
  closeInviteForm,
  toggleGroup,
  onInvite,
} = useInviteForm({ onUserCreated: load })


function formatDate(iso: string | null): string {
  return formatDateInSiteTime(iso, locale.value)
}


// --- Pending invites section ---------------------------------------------

const invites = ref<AdminInviteItem[]>([])
const invitesLoading = ref(false)
const invitesErrorMsg = ref<string | null>(null)

// Modal state — only one of these is non-null at a time.
const detailsInvite = ref<AdminInviteItem | null>(null)
const activateInviteRow = ref<AdminInviteItem | null>(null)
const activateDisplayName = ref('')
const activateInProgress = ref(false)
const activateError = ref<string | null>(null)
const revokeInviteRow = ref<AdminInviteItem | null>(null)
const revokeInProgress = ref(false)
const actionInProgressId = ref<number | null>(null)

async function loadInvites() {
  invitesLoading.value = true
  invitesErrorMsg.value = null
  try {
    const { data } = await listInvites({ state: 'all', page: 1, page_size: 100 })
    invites.value = data.items
  } catch (err) {
    invitesErrorMsg.value = describe(err)
  } finally {
    invitesLoading.value = false
  }
}

function inviteRowKey(inv: AdminInviteItem): number {
  return inv.id
}

function deriveDisplayName(email: string): string {
  const local = email.split('@')[0] ?? ''
  return local
    .replace(/[._]/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

async function onCopyLink(inv: AdminInviteItem) {
  actionInProgressId.value = inv.id
  try {
    const { data } = await regenerateInvite(inv.id)
    await navigator.clipboard.writeText(data.url)
    ui.pushToast(t('admin_users.invites.toast.link_copied'), 'success')
    void loadInvites()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    actionInProgressId.value = null
  }
}

async function onResend(inv: AdminInviteItem) {
  actionInProgressId.value = inv.id
  try {
    const { data } = await resendInvite(inv.id)
    ui.pushToast(
      t('admin_users.invites.toast.resent', {
        expires: formatDate(data.expires_at),
      }),
      'success',
    )
    void loadInvites()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    actionInProgressId.value = null
  }
}

function openDetails(inv: AdminInviteItem) {
  detailsInvite.value = inv
}
function closeDetails() {
  detailsInvite.value = null
}

function openRevoke(inv: AdminInviteItem) {
  revokeInviteRow.value = inv
}
function closeRevoke() {
  revokeInviteRow.value = null
}
async function onConfirmRevoke() {
  if (!revokeInviteRow.value) return
  revokeInProgress.value = true
  try {
    await revokeInvite(revokeInviteRow.value.id)
    ui.pushToast(t('admin_users.invites.toast.deleted'), 'success')
    closeRevoke()
    void loadInvites()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    revokeInProgress.value = false
  }
}

function openActivate(inv: AdminInviteItem) {
  activateInviteRow.value = inv
  activateDisplayName.value = deriveDisplayName(inv.email)
  activateError.value = null
}
function closeActivate() {
  activateInviteRow.value = null
  activateError.value = null
}
async function onConfirmActivate() {
  if (!activateInviteRow.value) return
  activateInProgress.value = true
  activateError.value = null
  try {
    await activateInvite(activateInviteRow.value.id, {
      display_name: activateDisplayName.value || undefined,
    })
    ui.pushToast(t('admin_users.invites.toast.activated'), 'success')
    closeActivate()
    // Refresh both lists — invite row vanishes, new user appears.
    void loadInvites()
    void load()
  } catch (err) {
    activateError.value = describe(err)
  } finally {
    activateInProgress.value = false
  }
}

onMounted(() => {
  void load()
  void loadInvites()
})
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
        <span class="fh-field-help">{{
          createDirectly ? t('admin_users.invite_email_help_direct') : t('admin_users.invite_email_help')
        }}</span>
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
      <label class="fh-checkbox-row">
        <input v-model="createDirectly" type="checkbox" />
        <span>{{ t('admin_users.invite_create_direct') }}</span>
      </label>
      <label v-if="createDirectly" class="fh-field">
        <span class="fh-field-label">{{ t('admin_users.invite_password_label') }}</span>
        <input
          v-model="invitePassword"
          class="fh-field-input"
          type="password"
          autocomplete="new-password"
          minlength="12"
          required
        />
        <span class="fh-field-help">{{ t('admin_users.invite_password_help') }}</span>
        <PasswordStrength :password="invitePassword" />
      </label>
      <div v-if="inviteError" class="fh-notice" data-tone="error">{{ inviteError }}</div>
      <div class="form-actions">
        <button
          type="submit"
          class="fh-btn"
          :disabled="
            inviting ||
            !inviteEmail ||
            !inviteDisplayName ||
            (createDirectly && invitePassword.length < 12)
          "
        >
          {{
            inviting
              ? t('common.loading')
              : createDirectly
              ? t('admin_users.invite_create_submit')
              : t('admin_users.invite_submit')
          }}
        </button>
        <button type="button" class="fh-btn-text" @click="closeInviteForm">
          {{ t('common.cancel') }}
        </button>
      </div>
    </form>

    <template v-if="!showInviteForm">

    <!-- Pending invites section -->
    <section v-if="invites.length > 0 || invitesLoading" class="invites-section">
      <h2 class="section-h2">{{ t('admin_users.invites.heading') }}</h2>
      <p class="fh-field-help section-help">{{ t('admin_users.invites.help') }}</p>
      <div v-if="invitesLoading" class="loading">{{ t('common.loading') }}</div>
      <div v-else-if="invitesErrorMsg" class="fh-notice" data-tone="error">{{ invitesErrorMsg }}</div>
      <table v-else class="invites-table">
        <thead>
          <tr>
            <th>{{ t('admin_users.invites.col.email') }}</th>
            <th>{{ t('admin_users.invites.col.role') }}</th>
            <th>{{ t('admin_users.invites.col.state') }}</th>
            <th>{{ t('admin_users.invites.col.invited_by') }}</th>
            <th>{{ t('admin_users.invites.col.sent') }}</th>
            <th>{{ t('admin_users.invites.col.expires') }}</th>
            <th class="actions-col">{{ t('admin_users.invites.col.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="inv in invites" :key="inviteRowKey(inv)">
            <td class="fh-mono">{{ inv.email }}</td>
            <td><span class="fh-mono role">{{ inv.target_role }}</span></td>
            <td>
              <span
                class="fh-pill"
                :data-state="inv.state === 'pending' ? 'warn' : 'danger'"
              >
                {{ t(`admin_users.invites.state.${inv.state}`) }}
              </span>
            </td>
            <td>
              <span v-if="inv.invited_by_display_name">{{ inv.invited_by_display_name }}</span>
              <span v-else class="subtle fh-mono">{{ t('admin_users.invites.invited_by_unknown') }}</span>
            </td>
            <td class="fh-mono">{{ formatDate(inv.created_at) }}</td>
            <td class="fh-mono">{{ formatDate(inv.expires_at) }}</td>
            <td class="actions-col">
              <button
                type="button"
                class="fh-btn-text inline-action"
                :disabled="actionInProgressId === inv.id"
                @click="onCopyLink(inv)"
              >
                {{ t('admin_users.invites.action.copy_link') }}
              </button>
              <button
                type="button"
                class="fh-btn-text inline-action"
                :disabled="actionInProgressId === inv.id"
                @click="onResend(inv)"
              >
                {{ t('admin_users.invites.action.resend') }}
              </button>
              <button
                type="button"
                class="fh-btn-text inline-action"
                @click="openDetails(inv)"
              >
                {{ t('admin_users.invites.action.details') }}
              </button>
              <button
                type="button"
                class="fh-btn-text inline-action"
                @click="openActivate(inv)"
              >
                {{ t('admin_users.invites.action.activate') }}
              </button>
              <button
                type="button"
                class="fh-btn-text inline-action danger"
                @click="openRevoke(inv)"
              >
                {{ t('admin_users.invites.action.delete') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <hr class="fh-rule section-divider" />
    </section>

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
          <th class="storage-col">{{ t('admin_users.col.storage') }}</th>
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
          <td class="fh-mono storage-col">
            {{ formatBytes(u.storage_used_bytes) }}<span
              v-if="u.quota_bytes"
              class="subtle"
            > / {{ formatBytes(u.quota_bytes) }}</span>
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

    <!-- Details modal -->
    <div v-if="detailsInvite" class="fh-modal-backdrop" @click.self="closeDetails" @keydown.escape="closeDetails">
      <div class="fh-modal" role="dialog" :aria-label="t('admin_users.invites.details.title')">
        <h2 class="modal-h2">{{ t('admin_users.invites.details.title') }}</h2>
        <dl class="details-list">
          <dt>{{ t('admin_users.invites.details.email') }}</dt>
          <dd class="fh-mono">{{ detailsInvite.email }}</dd>
          <dt>{{ t('admin_users.invites.details.target_role') }}</dt>
          <dd class="fh-mono">{{ detailsInvite.target_role }}</dd>
          <dt>{{ t('admin_users.invites.details.state') }}</dt>
          <dd>
            <span
              class="fh-pill"
              :data-state="detailsInvite.state === 'pending' ? 'warn' : 'danger'"
            >
              {{ t(`admin_users.invites.state.${detailsInvite.state}`) }}
            </span>
          </dd>
          <dt>{{ t('admin_users.invites.details.invited_by') }}</dt>
          <dd>
            <span v-if="detailsInvite.invited_by_display_name">
              {{ detailsInvite.invited_by_display_name }}
              <span class="subtle fh-mono">(id={{ detailsInvite.invited_by_id }})</span>
            </span>
            <span v-else class="subtle fh-mono">{{ t('admin_users.invites.invited_by_unknown') }}</span>
          </dd>
          <dt>{{ t('admin_users.invites.details.created_at') }}</dt>
          <dd class="fh-mono">{{ formatDate(detailsInvite.created_at) }}</dd>
          <dt>{{ t('admin_users.invites.details.expires_at') }}</dt>
          <dd class="fh-mono">{{ formatDate(detailsInvite.expires_at) }}</dd>
          <dt v-if="detailsInvite.initial_group_ids && detailsInvite.initial_group_ids.length">
            {{ t('admin_users.invites.details.initial_groups') }}
          </dt>
          <dd
            v-if="detailsInvite.initial_group_ids && detailsInvite.initial_group_ids.length"
            class="fh-mono"
          >
            {{ detailsInvite.initial_group_ids.join(', ') }}
          </dd>
        </dl>
        <div class="form-actions">
          <button type="button" class="fh-btn" @click="closeDetails">
            {{ t('common.close') }}
          </button>
        </div>
      </div>
    </div>

    <!-- Activate modal -->
    <div v-if="activateInviteRow" class="fh-modal-backdrop" @click.self="closeActivate" @keydown.escape="closeActivate">
      <div class="fh-modal" role="dialog" :aria-label="t('admin_users.invites.activate.title')">
        <h2 class="modal-h2">{{ t('admin_users.invites.activate.title') }}</h2>
        <p class="modal-body">
          {{
            t('admin_users.invites.activate.body', {
              email: activateInviteRow.email,
              role: activateInviteRow.target_role,
            })
          }}
        </p>
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_users.invites.activate.display_name_label') }}</span>
          <input
            v-model.trim="activateDisplayName"
            class="fh-field-input"
            type="text"
            maxlength="120"
          />
          <span class="fh-field-help">{{ t('admin_users.invites.activate.display_name_help') }}</span>
        </label>
        <div v-if="activateError" class="fh-notice" data-tone="error">{{ activateError }}</div>
        <div class="form-actions">
          <button
            type="button"
            class="fh-btn"
            :disabled="activateInProgress"
            @click="onConfirmActivate"
          >
            {{ activateInProgress ? t('common.loading') : t('admin_users.invites.activate.confirm') }}
          </button>
          <button type="button" class="fh-btn-text" @click="closeActivate">
            {{ t('common.cancel') }}
          </button>
        </div>
      </div>
    </div>

    <!-- Revoke confirmation -->
    <div v-if="revokeInviteRow" class="fh-modal-backdrop" @click.self="closeRevoke" @keydown.escape="closeRevoke">
      <div class="fh-modal fh-modal--small" role="dialog" :aria-label="t('admin_users.invites.delete.title')">
        <h2 class="modal-h2">{{ t('admin_users.invites.delete.title') }}</h2>
        <p class="modal-body">
          {{ t('admin_users.invites.delete.body', { email: revokeInviteRow.email }) }}
        </p>
        <div class="form-actions">
          <button
            type="button"
            class="fh-btn fh-btn--danger"
            :disabled="revokeInProgress"
            @click="onConfirmRevoke"
          >
            {{ revokeInProgress ? t('common.loading') : t('admin_users.invites.delete.confirm') }}
          </button>
          <button type="button" class="fh-btn-text" @click="closeRevoke">
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

.fh-checkbox-row {
  display: flex;
  align-items: center;
  gap: var(--fh-space-2);
  cursor: pointer;
  font-size: var(--fh-text-body-sm);
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

/* --- Pending invites section --- */

.invites-section {
  margin-bottom: var(--fh-space-5);
}

.section-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.1rem;
  margin: 0 0 var(--fh-space-1);
  color: var(--fh-ink);
}

.section-help {
  margin-bottom: var(--fh-space-3);
}

.section-divider {
  margin-top: var(--fh-space-4);
  margin-bottom: var(--fh-space-4);
}

.invites-table {
  width: 100%;
  border-collapse: collapse;
}

.invites-table th {
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

.invites-table td {
  padding: var(--fh-space-3) var(--fh-space-3) var(--fh-space-3) 0;
  border-bottom: var(--fh-border);
  vertical-align: middle;
  font-size: var(--fh-text-body-sm);
}

.actions-col {
  text-align: right;
  white-space: nowrap;
}

.inline-action {
  font-size: var(--fh-text-body-sm);
  margin-left: var(--fh-space-2);
}

.inline-action.danger {
  color: var(--fh-danger, #b91c1c);
}

/* --- Modals --- */

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

.details-list {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--fh-space-2) var(--fh-space-4);
  margin: 0 0 var(--fh-space-4);
}

.details-list dt {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
}

.details-list dd {
  margin: 0;
}
</style>
