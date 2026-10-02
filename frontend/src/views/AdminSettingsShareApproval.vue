<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { getShareApprovalSettings, updateShareApprovalSettings } from '@/api/admin'
  import { listGroups } from '@/api/groups'
  import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
  import PolicyGate from '@/components/admin/PolicyGate.vue'
  import { useApiError } from '@/composables/useApiError'
  import type { PolicyGateValue } from '@/composables/usePolicyAllowlist'
  import { useAuthStore } from '@/stores/auth'
  import { useUiStore } from '@/stores/ui'
  import type {
    ApprovalScope,
    ApproverMode,
    GroupResponse,
    ShareApprovalSettingsResponse,
  } from '@/types/api'

  const { t } = useI18n()
  const { describe } = useApiError()
  const ui = useUiStore()
  const auth = useAuthStore()

  const loading = ref(true)
  const saving = ref(false)
  const errorMsg = ref<string | null>(null)

  const enabled = ref(false)
  const gate = ref<PolicyGateValue<ApproverMode>>({ mode: 'admins_only', users: [], groups: [] })
  const scope = ref<ApprovalScope>('outbound')
  const exemptApprovers = ref(true)
  const allowContentReview = ref(true)
  const availableGroups = ref<GroupResponse[]>([])

  function applyResponse(data: ShareApprovalSettingsResponse) {
    enabled.value = data.enabled
    gate.value = {
      mode: data.approver_mode,
      users: data.approver_users,
      groups: data.approver_groups,
    }
    scope.value = data.scope
    exemptApprovers.value = data.exempt_approvers
    allowContentReview.value = data.allow_content_review
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
        approver_mode: gate.value.mode,
        approver_user_ids: gate.value.users.map((u) => u.id),
        approver_group_ids: gate.value.groups.map((g) => g.id),
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

  const modes = computed(() => [
    {
      value: 'admins_only' as const,
      label: t('admin_share_approval.mode.admins_only'),
      help: t('admin_share_approval.mode.admins_only_help'),
    },
    {
      value: 'employees_admins' as const,
      label: t('admin_share_approval.mode.employees_admins'),
      help: t('admin_share_approval.mode.employees_admins_help'),
    },
  ])

  // No inbox pill here, and no `everyone` mode, so the allowlist always shows.
  const labels = computed(() => ({
    mode: t('admin_share_approval.mode_label'),
    heading: t('admin_share_approval.allowlist_heading'),
    help: t('admin_share_approval.allowlist_help'),
    users: t('admin_share_approval.users_label'),
    usersPlaceholder: t('admin_share_approval.users_placeholder'),
    groups: t('admin_share_approval.groups_label'),
  }))

  const scopeOptions: { value: ApprovalScope; labelKey: string }[] = [
    { value: 'outbound', labelKey: 'admin_share_approval.scope.outbound' },
    { value: 'all', labelKey: 'admin_share_approval.scope.all' },
    { value: 'outbound_to_clients', labelKey: 'admin_share_approval.scope.outbound_to_clients' },
  ]

  /* Mirrors share_approval.policy_is_inert on the live form values so the admin
   * sees it while editing, not only after the save is refused: "every employee
   * may approve" plus "approvers' own shares are exempt" cancel out, and with
   * only outbound shares in scope nothing can ever queue. The backend refuses
   * the save; this explains why before they get there. */
  const policyIsInert = computed(
    () =>
      enabled.value &&
      gate.value.mode === 'employees_admins' &&
      exemptApprovers.value &&
      scope.value !== 'all',
  )

  onMounted(load)
</script>

<template>
  <div class="policy-page" data-density="operator">
    <AdminPageHeader />

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
        <PolicyGate v-model="gate" :modes="modes" :labels="labels" :groups="availableGroups" />

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

      <div v-if="policyIsInert" class="fh-notice" data-tone="warn">
        {{ t('admin_share_approval.inert_warning') }}
      </div>

      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving || policyIsInert">
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
  .mode-name {
    font-weight: 500;
  }
  .mode-help {
    font-size: var(--fh-text-body-sm);
    color: var(--fh-subtle);
  }
  .actions {
    display: flex;
    gap: var(--fh-space-3);
  }
</style>
