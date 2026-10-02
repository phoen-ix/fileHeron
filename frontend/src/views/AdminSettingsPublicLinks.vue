<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { getPublicLinkPolicy, updatePublicLinkPolicy } from '@/api/admin'
  import { listGroups } from '@/api/groups'
  import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
  import PolicyGate from '@/components/admin/PolicyGate.vue'
  import TunableFields from '@/components/admin/TunableFields.vue'
  import { useApiError } from '@/composables/useApiError'
  import type { PolicyGateValue } from '@/composables/usePolicyAllowlist'
  import { useAuthStore } from '@/stores/auth'
  import { useUiStore } from '@/stores/ui'
  import type { GroupResponse, PublicLinkPolicyMode, PublicLinkPolicyResponse } from '@/types/api'

  const { t } = useI18n()
  const { describe } = useApiError()
  const ui = useUiStore()
  const auth = useAuthStore()

  const loading = ref(true)
  const saving = ref(false)
  const errorMsg = ref<string | null>(null)

  const gate = ref<PolicyGateValue<PublicLinkPolicyMode>>({
    mode: 'everyone',
    users: [],
    groups: [],
  })
  const availableGroups = ref<GroupResponse[]>([])
  // Recipients with no account (v2.21.0) - both default off.
  const externalEnabled = ref(false)
  const externalOfferInvite = ref(false)

  function applyResponse(data: PublicLinkPolicyResponse) {
    gate.value = { mode: data.mode, users: data.allowed_users, groups: data.allowed_groups }
    externalEnabled.value = data.external_recipients_enabled
    externalOfferInvite.value = data.external_recipients_offer_invite
  }

  async function load() {
    loading.value = true
    errorMsg.value = null
    try {
      const [{ data: policy }, { data: groups }] = await Promise.all([
        getPublicLinkPolicy(),
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
      const { data } = await updatePublicLinkPolicy({
        mode: gate.value.mode,
        allowed_user_ids: gate.value.users.map((u) => u.id),
        allowed_group_ids: gate.value.groups.map((g) => g.id),
        external_recipients_enabled: externalEnabled.value,
        // Stored as set, but only meaningful while the first switch is on -
        // turning that off also turns this off, so re-enabling starts quiet.
        external_recipients_offer_invite: externalEnabled.value && externalOfferInvite.value,
      })
      applyResponse(data)
      // Refresh /me so the local can_create_public_link flag reflects the
      // change immediately (matters when the admin just gated themselves
      // out of self-creation, though admin always passes anyway).
      await auth.refreshMe()
      ui.pushToast(t('admin_public_link_policy.saved_toast'), 'success')
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      saving.value = false
    }
  }

  const modes = computed(() => [
    {
      value: 'everyone' as const,
      label: t('admin_public_link_policy.mode.everyone'),
      help: t('admin_public_link_policy.mode.everyone_help'),
    },
    {
      value: 'employees_admins' as const,
      label: t('admin_public_link_policy.mode.employees_admins'),
      help: t('admin_public_link_policy.mode.employees_admins_help'),
    },
    {
      value: 'admins_only' as const,
      label: t('admin_public_link_policy.mode.admins_only'),
      help: t('admin_public_link_policy.mode.admins_only_help'),
    },
  ])

  const labels = computed(() => ({
    mode: t('admin_public_link_policy.mode_label'),
    heading: t('admin_public_link_policy.allowlist_heading'),
    help: t('admin_public_link_policy.allowlist_help'),
    users: t('admin_public_link_policy.users_label'),
    usersPlaceholder: t('admin_public_link_policy.users_placeholder'),
    groups: t('admin_public_link_policy.groups_label'),
    inbox: t('admin_public_link_policy.groups_inbox'),
  }))

  onMounted(load)
</script>

<template>
  <div class="policy-page" data-density="operator">
    <AdminPageHeader />

    <p class="fh-field-help intro">{{ t('admin_public_link_policy.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="policy-form" @submit.prevent="onSave">
      <PolicyGate v-model="gate" :modes="modes" :labels="labels" :groups="availableGroups" />

      <fieldset id="external-recipients" class="mode-fieldset">
        <legend class="fh-field-label">{{ t('admin_public_link_policy.external.title') }}</legend>
        <p class="fh-field-help">{{ t('admin_public_link_policy.external.help') }}</p>
        <label class="mode-option">
          <input v-model="externalEnabled" type="checkbox" data-testid="external-enabled" />
          <span>
            <span class="mode-name">{{
              t('admin_public_link_policy.external.enabled_label')
            }}</span>
            <span class="mode-help">{{ t('admin_public_link_policy.external.enabled_help') }}</span>
          </span>
        </label>
        <label class="mode-option">
          <input
            v-model="externalOfferInvite"
            type="checkbox"
            :disabled="!externalEnabled"
            data-testid="external-offer-invite"
          />
          <span>
            <span class="mode-name">{{
              t('admin_public_link_policy.external.offer_invite_label')
            }}</span>
            <span class="mode-help">{{
              t('admin_public_link_policy.external.offer_invite_help')
            }}</span>
          </span>
        </label>
      </fieldset>

      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>

    <hr class="fh-rule" />

    <section class="brute-force">
      <h2 class="section-h2">{{ t('admin_public_link_policy.brute_force_title') }}</h2>
      <p class="fh-field-help">{{ t('admin_public_link_policy.brute_force_help') }}</p>
      <TunableFields route="admin-settings-public-links" :headings="false" />
    </section>
  </div>
</template>

<style scoped>
  .section-h2 {
    font-family: var(--fh-font-display);
    font-size: 1.25rem;
    font-weight: 400;
    margin: 0 0 var(--fh-space-2);
  }

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
  .actions {
    display: flex;
    gap: var(--fh-space-3);
  }
</style>
