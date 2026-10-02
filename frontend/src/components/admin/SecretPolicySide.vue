<!-- One policy gate on the Secrets policy tab (v2.24.0): a mode, plus the
     users and groups always allowed on top of it. The page has two - who may
     send a secret at all, and who may send one out of the organisation - with
     the same shape as the public-link policy. -->
<template>
  <fieldset class="policy-side">
    <legend class="fh-field-label">{{ legend }}</legend>
    <p class="fh-field-help">{{ help }}</p>
    <label v-for="opt in modes" :key="opt" class="mode-option">
      <input
        type="radio"
        :name="name"
        :value="opt"
        :checked="modelValue.mode === opt"
        :disabled="disabled"
        @change="update({ mode: opt })"
      />
      <span>
        <span class="mode-name">{{ t(`admin_settings_secrets.mode.${opt}`) }}</span>
        <span class="mode-help">{{ modeHelp(opt) }}</span>
      </span>
    </label>

    <div v-if="modelValue.mode !== 'everyone'" class="allowlist">
      <span class="fh-field-label">{{ t('admin_settings_secrets.allow_users') }}</span>
      <ul v-if="modelValue.users.length" class="picked-list">
        <li v-for="u in modelValue.users" :key="u.id" class="picked-row">
          <span class="row-name">{{ u.display_name }}</span>
          <span class="fh-mono row-hint">{{ rowHint(u) }}</span>
          <button
            type="button"
            class="fh-btn-text danger"
            :disabled="disabled"
            @click="removeUser(u.id)"
          >
            {{ t('common.remove') }}
          </button>
        </li>
      </ul>
      <input
        v-model.trim="query"
        type="search"
        class="fh-field-input"
        autocomplete="off"
        :disabled="disabled"
        :aria-label="t('admin_settings_secrets.users_placeholder')"
        :placeholder="t('admin_settings_secrets.users_placeholder')"
      />
      <ul v-if="suggestions.length" class="suggestions">
        <li v-for="u in suggestions" :key="u.user_id">
          <button type="button" class="suggest" @click="addUser(u)">
            <span class="row-name">{{ u.display_name }}</span>
            <span class="fh-mono row-hint">{{ u.email }} · {{ u.role }}</span>
          </button>
        </li>
      </ul>

      <template v-if="groups.length">
        <span class="fh-field-label">{{ t('admin_settings_secrets.allow_groups') }}</span>
        <ul class="group-checks">
          <li v-for="g in groups" :key="g.id">
            <label class="group-check">
              <input
                type="checkbox"
                :checked="modelValue.groups.some((x) => x.id === g.id)"
                :disabled="disabled"
                @change="toggleGroup(g)"
              />
              <span class="fh-mono">{{ g.name }}</span>
            </label>
          </li>
        </ul>
      </template>
    </div>
  </fieldset>
</template>

<script setup lang="ts">
  import { useI18n } from 'vue-i18n'

  import { toggledGroups, usePolicyAllowlist } from '@/composables/usePolicyAllowlist'
  import type {
    GroupResponse,
    SecretAllowedGroup,
    SecretAllowedUser,
    SecretPolicyMode,
  } from '@/types/api'

  interface PolicySideModel {
    mode: SecretPolicyMode
    users: SecretAllowedUser[]
    groups: SecretAllowedGroup[]
  }

  const props = defineProps<{
    modelValue: PolicySideModel
    modes: SecretPolicyMode[]
    groups: GroupResponse[]
    name: string
    legend: string
    help: string
    disabled?: boolean
    /** The out-of-the-organisation gate, which a client never passes - not
     *  through the mode and not through the allowlist. */
    outside?: boolean
  }>()
  const emit = defineEmits<{ 'update:modelValue': [value: PolicySideModel] }>()
  const { t } = useI18n()

  function modeHelp(opt: SecretPolicyMode): string {
    if (!props.outside) return t(`admin_settings_secrets.mode.${opt}_help`)
    return opt === 'admins_only'
      ? t('admin_settings_secrets.mode_outside.admins_only_help')
      : t('admin_settings_secrets.mode_outside.employees_admins_help')
  }

  // A client can be on this list only from before a role change (or the API);
  // say that it does nothing rather than let it read as a working exception.
  function rowHint(u: SecretAllowedUser): string {
    const hint = `${u.email} · ${u.role}`
    return props.outside && u.role === 'client'
      ? `${hint} · ${t('admin_settings_secrets.client_never')}`
      : hint
  }

  function update(patch: Partial<PolicySideModel>) {
    emit('update:modelValue', { ...props.modelValue, ...patch })
  }

  // The out-of-the-organisation gate never offers a client: no mode or
  // allowlist entry lets one send outside.
  const { query, suggestions, addUser, removeUser } = usePolicyAllowlist({
    users: () => props.modelValue.users,
    setUsers: (users) => update({ users }),
    exclude: (u) => !!props.outside && u.role === 'client',
  })

  function toggleGroup(g: GroupResponse) {
    update({ groups: toggledGroups(props.modelValue.groups, g) })
  }
</script>

<style scoped>
  .policy-side {
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
  .allowlist {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-2);
    margin-top: var(--fh-space-2);
  }
  .picked-list,
  .suggestions,
  .group-checks {
    list-style: none;
    margin: 0;
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
  .suggestions {
    border: 1px solid var(--fh-hairline);
    background: var(--fh-paper-raised);
    max-height: 220px;
    overflow-y: auto;
  }
  .suggest {
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
  .suggest:hover {
    background: var(--fh-paper-sunk);
  }
  .group-check {
    display: inline-flex;
    align-items: center;
    gap: var(--fh-space-2);
    cursor: pointer;
  }
  .row-name {
    font-weight: 500;
  }
  .row-hint {
    font-size: var(--fh-text-mono-sm);
    color: var(--fh-subtle);
  }
  .fh-btn-text.danger {
    color: var(--fh-danger);
  }
</style>
