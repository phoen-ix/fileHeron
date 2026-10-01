<script setup lang="ts">
  /* Admin › Sharing › Secrets › Policy (v2.24.0). The instance switch, the two
   * policy gates, the wrong-passphrase rule, and - through TunableFields - the
   * ceilings and throttle numbers, which the registry's one writer saves. A tab
   * of AdminTabShell, which renders the page header. */
  import { onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { listGroups } from '@/api/groups'
  import { getSecretPolicy, updateSecretPolicy } from '@/api/secrets'
  import SecretPolicySide from '@/components/admin/SecretPolicySide.vue'
  import TunableFields from '@/components/admin/TunableFields.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useAuthStore } from '@/stores/auth'
  import { useUiStore } from '@/stores/ui'
  import type {
    GroupResponse,
    PassphraseFailureMode,
    SecretAllowedGroup,
    SecretAllowedUser,
    SecretPolicyMode,
    SecretPolicyResponse,
    SecretPolicySide as SideResponse,
  } from '@/types/api'

  interface SideModel {
    mode: SecretPolicyMode
    users: SecretAllowedUser[]
    groups: SecretAllowedGroup[]
  }

  const { t } = useI18n()
  const { describe } = useApiError()
  const ui = useUiStore()
  const auth = useAuthStore()

  const loading = ref(true)
  const saving = ref(false)
  const errorMsg = ref<string | null>(null)
  const enabled = ref(false)
  const send = ref<SideModel>({ mode: 'everyone', users: [], groups: [] })
  const external = ref<SideModel>({ mode: 'employees_admins', users: [], groups: [] })
  const failureMode = ref<PassphraseFailureMode>('lock')
  const groups = ref<GroupResponse[]>([])

  // Out of the organisation is never a client's to do, so "everyone" would
  // only mean "all staff" here - offered as such, not as a third option.
  const EXTERNAL_MODES: SecretPolicyMode[] = ['employees_admins', 'admins_only']
  const SEND_MODES: SecretPolicyMode[] = ['everyone', 'employees_admins', 'admins_only']
  const FAILURE_MODES: PassphraseFailureMode[] = ['lock', 'burn']

  function side(s: SideResponse, external = false): SideModel {
    return {
      mode: external && s.mode === 'everyone' ? 'employees_admins' : s.mode,
      users: s.allowed_users,
      groups: s.allowed_groups,
    }
  }

  function apply(data: SecretPolicyResponse) {
    enabled.value = data.enabled
    send.value = side(data.send)
    external.value = side(data.external, true)
    failureMode.value = data.passphrase_failure_mode
  }

  async function load() {
    loading.value = true
    errorMsg.value = null
    try {
      const [{ data }, { data: g }] = await Promise.all([getSecretPolicy(), listGroups()])
      apply(data)
      groups.value = g.items
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      loading.value = false
    }
  }

  function payload(s: SideModel) {
    return {
      mode: s.mode,
      allowed_user_ids: s.users.map((u) => u.id),
      allowed_group_ids: s.groups.map((g) => g.id),
    }
  }

  async function onSave() {
    saving.value = true
    errorMsg.value = null
    try {
      const { data } = await updateSecretPolicy({
        enabled: enabled.value,
        send: payload(send.value),
        external: payload(external.value),
        passphrase_failure_mode: failureMode.value,
      })
      apply(data)
      await auth.refreshMe()
      ui.pushToast(t('admin_settings_secrets.saved_toast'), 'success')
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      saving.value = false
    }
  }

  onMounted(load)
</script>

<template>
  <div class="secret-policy" data-density="operator">
    <p class="fh-field-help intro">{{ t('admin_settings_secrets.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="policy-form" @submit.prevent="onSave">
      <label id="secrets-enabled" class="mode-option">
        <input v-model="enabled" type="checkbox" data-testid="secrets-enabled" />
        <span>
          <span class="mode-name">{{ t('admin_settings_secrets.enabled_label') }}</span>
          <span class="mode-help">{{ t('admin_settings_secrets.enabled_help') }}</span>
        </span>
      </label>

      <SecretPolicySide
        v-model="send"
        name="send-mode"
        :modes="SEND_MODES"
        :groups="groups"
        :legend="t('admin_settings_secrets.send_title')"
        :help="t('admin_settings_secrets.send_help')"
        :disabled="saving"
      />
      <SecretPolicySide
        v-model="external"
        name="external-mode"
        outside
        :modes="EXTERNAL_MODES"
        :groups="groups"
        :legend="t('admin_settings_secrets.external_title')"
        :help="t('admin_settings_secrets.external_help')"
        :disabled="saving"
      />

      <fieldset class="failure-mode">
        <legend class="fh-field-label">{{ t('admin_settings_secrets.failure_title') }}</legend>
        <p class="fh-field-help">{{ t('admin_settings_secrets.failure_help') }}</p>
        <label v-for="m in FAILURE_MODES" :key="m" class="mode-option">
          <input v-model="failureMode" type="radio" name="failure-mode" :value="m" />
          <span>
            <span class="mode-name">{{ t(`admin_settings_secrets.failure.${m}`) }}</span>
            <span class="mode-help">{{ t(`admin_settings_secrets.failure.${m}_help`) }}</span>
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

    <section class="limits">
      <h2 class="section-h2">{{ t('admin_settings_secrets.limits_title') }}</h2>
      <p class="fh-field-help">{{ t('admin_settings_secrets.limits_help') }}</p>
      <TunableFields route="admin-settings-secrets" :headings="false" />
    </section>
  </div>
</template>

<style scoped>
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
  }
  .failure-mode {
    border: 1px solid var(--fh-rule);
    border-radius: var(--fh-radius-sm);
    padding: var(--fh-space-3);
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-2);
    margin: 0;
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
  .section-h2 {
    font-family: var(--fh-font-display);
    font-size: 1.25rem;
    font-weight: 400;
    margin: 0 0 var(--fh-space-2);
  }
  .actions {
    display: flex;
    gap: var(--fh-space-3);
  }
</style>
