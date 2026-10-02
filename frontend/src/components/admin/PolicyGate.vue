<!-- A "who may" policy: a base mode, plus the users and groups always allowed
     on top of it. The API-token, public-link and share-approval pages each
     carried this block as their own copy of markup and script; the wording
     differs per page, so every label arrives already translated. Two root
     elements on purpose: the page's form lays them out as its own children. -->
<script setup lang="ts" generic="M extends string">
  import { computed } from 'vue'
  import { useI18n } from 'vue-i18n'

  import {
    type PolicyGateGroup,
    type PolicyGateValue,
    toggledGroups,
    usePolicyAllowlist,
  } from '@/composables/usePolicyAllowlist'
  import type { GroupResponse } from '@/types/api'

  defineOptions({ inheritAttrs: false })

  const props = defineProps<{
    modelValue: PolicyGateValue<M>
    modes: { value: M; label: string; help: string }[]
    labels: {
      mode: string
      heading: string
      help: string
      users: string
      usersPlaceholder: string
      groups: string
      /** Pill on the company-inbox group; omitted = no pill. */
      inbox?: string
    }
    groups: GroupResponse[]
  }>()
  const emit = defineEmits<{ 'update:modelValue': [value: PolicyGateValue<M>] }>()
  const { t } = useI18n()

  function update(patch: Partial<PolicyGateValue<M>>) {
    emit('update:modelValue', { ...props.modelValue, ...patch })
  }

  const mode = computed({
    get: () => props.modelValue.mode,
    set: (m: M) => update({ mode: m }),
  })

  const { query, suggestions, addUser, removeUser } = usePolicyAllowlist({
    users: () => props.modelValue.users,
    setUsers: (users) => update({ users }),
  })

  function toggleGroup(g: PolicyGateGroup) {
    update({ groups: toggledGroups(props.modelValue.groups, g) })
  }
</script>

<template>
  <fieldset class="mode-fieldset">
    <legend class="fh-field-label">{{ labels.mode }}</legend>
    <label v-for="opt in modes" :key="opt.value" class="mode-option">
      <input v-model="mode" type="radio" :value="opt.value" />
      <span>
        <span class="mode-name">{{ opt.label }}</span>
        <span class="mode-help">{{ opt.help }}</span>
      </span>
    </label>
  </fieldset>

  <section v-if="modelValue.mode !== 'everyone'" class="allowlist">
    <h2 class="form-h2">{{ labels.heading }}</h2>
    <p class="fh-field-help">{{ labels.help }}</p>

    <div class="fh-field">
      <span class="fh-field-label">{{ labels.users }}</span>
      <ul v-if="modelValue.users.length > 0" class="picked-list">
        <li v-for="u in modelValue.users" :key="u.id" class="picked-row">
          <span class="row-name">{{ u.display_name }}</span>
          <span class="fh-mono row-hint">{{ u.email }} · {{ u.role }}</span>
          <button type="button" class="fh-btn-text danger" @click="removeUser(u.id)">
            {{ t('common.remove') }}
          </button>
        </li>
      </ul>
      <input
        v-model.trim="query"
        :aria-label="labels.usersPlaceholder"
        type="search"
        class="fh-field-input"
        autocomplete="off"
        :placeholder="labels.usersPlaceholder"
      />
      <ul v-if="suggestions.length > 0" class="user-suggestions">
        <li v-for="u in suggestions" :key="u.user_id">
          <button type="button" class="user-suggest" @click="addUser(u)">
            <span class="row-name">{{ u.display_name }}</span>
            <span class="fh-mono row-hint">{{ u.email }} · {{ u.role }}</span>
          </button>
        </li>
      </ul>
    </div>

    <div v-if="groups.length > 0" class="fh-field">
      <span class="fh-field-label">{{ labels.groups }}</span>
      <ul class="group-checks">
        <li v-for="g in groups" :key="g.id">
          <label class="group-check">
            <input
              type="checkbox"
              :checked="modelValue.groups.some((x) => x.id === g.id)"
              @change="toggleGroup(g)"
            />
            <span class="group-name">{{ g.name }}</span>
            <span v-if="labels.inbox && g.is_company_inbox" class="fh-pill">
              {{ labels.inbox }}
            </span>
          </label>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
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
</style>
