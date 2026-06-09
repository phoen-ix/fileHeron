<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { getShareApprovalSettings, updateShareApprovalSettings } from '@/api/admin'
import { listGroups } from '@/api/groups'
import { searchUsers } from '@/api/users'
import { useApiError } from '@/composables/useApiError'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type {
  ApprovalScope,
  ApproverGroupRef,
  ApproverMode,
  ApproverUserRef,
  GroupResponse,
  ShareApprovalSettingsResponse,
  UserSearchItem,
} from '@/types/api'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()
const auth = useAuthStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)

const enabled = ref(false)
const mode = ref<ApproverMode>('admins_only')
const scope = ref<ApprovalScope>('outbound')
const exemptApprovers = ref(true)
const allowContentReview = ref(true)
const allowedUsers = ref<ApproverUserRef[]>([])
const allowedGroups = ref<ApproverGroupRef[]>([])
const availableGroups = ref<GroupResponse[]>([])

const userQuery = ref('')
const userSuggestions = ref<UserSearchItem[]>([])
let userSearchTimer: ReturnType<typeof setTimeout> | null = null

watch(userQuery, () => {
  if (userSearchTimer) clearTimeout(userSearchTimer)
  if (!userQuery.value || userQuery.value.length < 2) {
    userSuggestions.value = []
    return
  }
  userSearchTimer = setTimeout(async () => {
    try {
      const { data } = await searchUsers(userQuery.value)
      userSuggestions.value = data.items.filter(
        (u) => !allowedUsers.value.some((au) => au.id === u.user_id),
      )
    } catch {
      userSuggestions.value = []
    }
  }, 200)
})

function pickUser(u: UserSearchItem) {
  allowedUsers.value = [
    ...allowedUsers.value,
    { id: u.user_id, display_name: u.display_name, email: u.email, role: u.role },
  ]
  userQuery.value = ''
  userSuggestions.value = []
}

function removeUser(id: number) {
  allowedUsers.value = allowedUsers.value.filter((u) => u.id !== id)
}

function toggleGroup(g: GroupResponse) {
  const idx = allowedGroups.value.findIndex((x) => x.id === g.id)
  if (idx === -1) {
    allowedGroups.value = [...allowedGroups.value, { id: g.id, name: g.name }]
  } else {
    allowedGroups.value = allowedGroups.value.filter((x) => x.id !== g.id)
  }
}

function applyResponse(data: ShareApprovalSettingsResponse) {
  enabled.value = data.enabled
  mode.value = data.approver_mode
  scope.value = data.scope
  exemptApprovers.value = data.exempt_approvers
  allowContentReview.value = data.allow_content_review
  allowedUsers.value = data.approver_users
  allowedGroups.value = data.approver_groups
}

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const [{ data: policy }, { data: groups }] = await Promise.all([
      getShareApprovalSettings(),
      listGroups(),
    ])
    applyResponse(policy)
    availableGroups.value = groups.items
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onSave() {
  saving.value = true
  errorMsg.value = null
  try {
    const { data } = await updateShareApprovalSettings({
      enabled: enabled.value,
      approver_mode: mode.value,
      approver_user_ids: allowedUsers.value.map((u) => u.id),
      approver_group_ids: allowedGroups.value.map((g) => g.id),
      scope: scope.value,
      exempt_approvers: exemptApprovers.value,
      allow_content_review: allowContentReview.value,
    })
    applyResponse(data)
    // Refresh /me so the local can_approve_shares flag (nav + UI) updates.
    await auth.refreshMe()
    ui.pushToast(t('admin_share_approval.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

const modeOptions: { value: ApproverMode; labelKey: string; helpKey: string }[] = [
  {
    value: 'admins_only',
    labelKey: 'admin_share_approval.mode.admins_only',
    helpKey: 'admin_share_approval.mode.admins_only_help',
  },
  {
    value: 'employees_admins',
    labelKey: 'admin_share_approval.mode.employees_admins',
    helpKey: 'admin_share_approval.mode.employees_admins_help',
  },
]

const scopeOptions: { value: ApprovalScope; labelKey: string }[] = [
  { value: 'outbound', labelKey: 'admin_share_approval.scope.outbound' },
  { value: 'all', labelKey: 'admin_share_approval.scope.all' },
  { value: 'outbound_to_clients', labelKey: 'admin_share_approval.scope.outbound_to_clients' },
]

onMounted(load)
</script>

<template>
  <div class="policy-page" data-density="operator">
    <span class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_share_approval.title') }}
    </span>

    <p class="fh-field-help intro">{{ t('admin_share_approval.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="policy-form" @submit.prevent="onSave">
      <label class="toggle-row">
        <input v-model="enabled" type="checkbox" />
        <span>
          <span class="mode-name">{{ t('admin_share_approval.enable_label') }}</span>
          <span class="mode-help">{{ t('admin_share_approval.enable_help') }}</span>
        </span>
      </label>

      <template v-if="enabled">
        <fieldset class="mode-fieldset">
          <legend class="fh-field-label">{{ t('admin_share_approval.mode_label') }}</legend>
          <label v-for="opt in modeOptions" :key="opt.value" class="mode-option">
            <input v-model="mode" type="radio" :value="opt.value" />
            <span>
              <span class="mode-name">{{ t(opt.labelKey) }}</span>
              <span class="mode-help">{{ t(opt.helpKey) }}</span>
            </span>
          </label>
        </fieldset>

        <section class="allowlist">
          <h2 class="form-h2">{{ t('admin_share_approval.allowlist_heading') }}</h2>
          <p class="fh-field-help">{{ t('admin_share_approval.allowlist_help') }}</p>

          <div class="fh-field">
            <span class="fh-field-label">{{ t('admin_share_approval.users_label') }}</span>
            <ul v-if="allowedUsers.length > 0" class="picked-list">
              <li v-for="u in allowedUsers" :key="u.id" class="picked-row">
                <span class="row-name">{{ u.display_name }}</span>
                <span class="fh-mono row-hint">{{ u.email }} · {{ u.role }}</span>
                <button type="button" class="fh-btn-text danger" @click="removeUser(u.id)">
                  {{ t('common.remove') }}
                </button>
              </li>
            </ul>
            <input
              v-model.trim="userQuery"
              type="search"
              class="fh-field-input"
              autocomplete="off"
              :placeholder="t('admin_share_approval.users_placeholder')"
            />
            <ul v-if="userSuggestions.length > 0" class="user-suggestions">
              <li v-for="u in userSuggestions" :key="u.user_id">
                <button type="button" class="user-suggest" @click="pickUser(u)">
                  <span class="row-name">{{ u.display_name }}</span>
                  <span class="fh-mono row-hint">{{ u.email }} · {{ u.role }}</span>
                </button>
              </li>
            </ul>
          </div>

          <div v-if="availableGroups.length > 0" class="fh-field">
            <span class="fh-field-label">{{ t('admin_share_approval.groups_label') }}</span>
            <ul class="group-checks">
              <li v-for="g in availableGroups" :key="g.id">
                <label class="group-check">
                  <input
                    type="checkbox"
                    :checked="allowedGroups.some((x) => x.id === g.id)"
                    @change="toggleGroup(g)"
                  />
                  <span class="group-name">{{ g.name }}</span>
                </label>
              </li>
            </ul>
          </div>
        </section>

        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_share_approval.scope_label') }}</span>
          <select v-model="scope" class="fh-field-input">
            <option v-for="opt in scopeOptions" :key="opt.value" :value="opt.value">
              {{ t(opt.labelKey) }}
            </option>
          </select>
        </label>

        <label class="toggle-row">
          <input v-model="exemptApprovers" type="checkbox" />
          <span>
            <span class="mode-name">{{ t('admin_share_approval.exempt_label') }}</span>
            <span class="mode-help">{{ t('admin_share_approval.exempt_help') }}</span>
          </span>
        </label>

        <label class="toggle-row">
          <input v-model="allowContentReview" type="checkbox" />
          <span>
            <span class="mode-name">{{ t('admin_share_approval.review_label') }}</span>
            <span class="mode-help">{{ t('admin_share_approval.review_help') }}</span>
          </span>
        </label>
      </template>

      <div v-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.policy-page {
  max-width: none;
}
.intro {
  margin: var(--fh-space-2) 0 var(--fh-space-3);
  max-width: 64ch;
}
.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-4) 0;
}
.policy-form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-4);
  margin-top: var(--fh-space-3);
}
.toggle-row {
  display: flex;
  gap: var(--fh-space-2);
  align-items: flex-start;
  cursor: pointer;
  padding: var(--fh-space-3);
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
}
.toggle-row > span {
  display: flex;
  flex-direction: column;
}
.mode-fieldset {
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}
.mode-option {
  display: flex;
  align-items: flex-start;
  gap: var(--fh-space-2);
  cursor: pointer;
}
.mode-option > span {
  display: flex;
  flex-direction: column;
}
.mode-name {
  font-weight: 500;
}
.mode-help {
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
}
.allowlist {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-3);
}
.form-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.25rem;
  margin: 0;
}
.picked-list {
  list-style: none;
  margin: 0 0 var(--fh-space-2);
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
}
.picked-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) auto;
  gap: var(--fh-space-3);
  align-items: center;
  padding: var(--fh-space-2) var(--fh-space-3);
  background: var(--fh-paper-raised);
  border: 1px solid var(--fh-hairline);
  border-radius: var(--fh-radius-sm);
}
.row-name {
  font-weight: 500;
}
.row-hint {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}
.user-suggestions {
  list-style: none;
  margin: var(--fh-space-1) 0 0;
  padding: 0;
  border: 1px solid var(--fh-hairline);
  background: var(--fh-paper-raised);
  max-height: 220px;
  overflow-y: auto;
}
.user-suggest {
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
.user-suggest:hover {
  background: var(--fh-paper-sunk);
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
.fh-btn-text.danger {
  color: var(--fh-danger);
}
.actions {
  display: flex;
  gap: var(--fh-space-3);
}
</style>
