<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { getTokenPolicy, updateTokenPolicy } from '@/api/admin'
  import { listGroups } from '@/api/groups'
  import PolicyGate from '@/components/admin/PolicyGate.vue'
  import { useApiError } from '@/composables/useApiError'
  import type { PolicyGateValue } from '@/composables/usePolicyAllowlist'
  import { useUiStore } from '@/stores/ui'
  import type { GroupResponse, TokenPolicyMode, TokenPolicyResponse } from '@/types/api'

  const { t } = useI18n()
  const { describe } = useApiError()
  const ui = useUiStore()

  const loading = ref(true)
  const saving = ref(false)
  const errorMsg = ref<string | null>(null)

  const gate = ref<PolicyGateValue<TokenPolicyMode>>({ mode: 'everyone', users: [], groups: [] })
  const availableGroups = ref<GroupResponse[]>([])

  function applyResponse(data: TokenPolicyResponse) {
    gate.value = { mode: data.mode, users: data.allowed_users, groups: data.allowed_groups }
  }

  async function load() {
    loading.value = true
    errorMsg.value = null
    try {
      const [{ data: policy }, { data: groups }] = await Promise.all([
        getTokenPolicy(),
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
      const { data } = await updateTokenPolicy({
        mode: gate.value.mode,
        allowed_user_ids: gate.value.users.map((u) => u.id),
        allowed_group_ids: gate.value.groups.map((g) => g.id),
      })
      applyResponse(data)
      ui.pushToast(t('admin_token_policy.saved_toast'), 'success')
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      saving.value = false
    }
  }

  const modes = computed(() => [
    {
      value: 'everyone' as const,
      label: t('admin_token_policy.mode.everyone'),
      help: t('admin_token_policy.mode.everyone_help'),
    },
    {
      value: 'employees_admins' as const,
      label: t('admin_token_policy.mode.employees_admins'),
      help: t('admin_token_policy.mode.employees_admins_help'),
    },
    {
      value: 'admins_only' as const,
      label: t('admin_token_policy.mode.admins_only'),
      help: t('admin_token_policy.mode.admins_only_help'),
    },
  ])

  const labels = computed(() => ({
    mode: t('admin_token_policy.mode_label'),
    heading: t('admin_token_policy.allowlist_heading'),
    help: t('admin_token_policy.allowlist_help'),
    users: t('admin_token_policy.users_label'),
    usersPlaceholder: t('admin_token_policy.users_placeholder'),
    groups: t('admin_token_policy.groups_label'),
    inbox: t('admin_token_policy.groups_inbox'),
  }))

  onMounted(load)
</script>

<template>
  <div class="policy-page" data-density="operator">
    <p class="fh-field-help intro">{{ t('admin_token_policy.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="policy-form" @submit.prevent="onSave">
      <PolicyGate v-model="gate" :modes="modes" :labels="labels" :groups="availableGroups" />

      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

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

  .actions {
    display: flex;
    gap: var(--fh-space-3);
  }
</style>
