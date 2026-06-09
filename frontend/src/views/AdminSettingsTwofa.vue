<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { getTwofaPolicy, updateTwofaPolicy } from '@/api/admin'
import { listGroups } from '@/api/groups'
import { useApiError } from '@/composables/useApiError'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type {
  GroupResponse,
  RequiredGroupRef,
  TwofaPolicyResponse,
} from '@/types/api'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()
const auth = useAuthStore()
const router = useRouter()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)
const isKvOverridden = ref(false)

const requiredRoles = ref<Set<string>>(new Set())
const requiredGroups = ref<RequiredGroupRef[]>([])
const availableGroups = ref<GroupResponse[]>([])

const ROLE_KEYS = [
  { value: 'admin', labelKey: 'admin_twofa_policy.role.admin' },
  { value: 'employee', labelKey: 'admin_twofa_policy.role.employee' },
  { value: 'client', labelKey: 'admin_twofa_policy.role.client' },
]

function toggleRole(value: string) {
  const next = new Set(requiredRoles.value)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  requiredRoles.value = next
}

function toggleGroup(g: GroupResponse) {
  const has = requiredGroups.value.some((x) => x.id === g.id)
  if (has) {
    requiredGroups.value = requiredGroups.value.filter((x) => x.id !== g.id)
  } else {
    requiredGroups.value = [
      ...requiredGroups.value,
      { id: g.id, name: g.name, is_company_inbox: g.is_company_inbox },
    ]
  }
}

function applyResponse(data: TwofaPolicyResponse) {
  requiredRoles.value = new Set(data.required_roles)
  requiredGroups.value = data.required_groups
  isKvOverridden.value = data.is_kv_overridden
}

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const [{ data: policy }, { data: groups }] = await Promise.all([
      getTwofaPolicy(),
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
    const { data } = await updateTwofaPolicy({
      required_roles: [...requiredRoles.value],
      required_group_ids: requiredGroups.value.map((g) => g.id),
    })
    applyResponse(data)
    // Refresh /me so the running session sees the new requires_2fa
    // value (relevant if the admin just tightened the policy on
    // themselves or relaxed it).
    await auth.refreshMe()
    ui.pushToast(t('admin_twofa_policy.saved_toast'), 'success')
    // Saving admin just made themselves required + isn't enrolled -
    // jump straight into the QR enrolment wizard so they don't need
    // to navigate manually (the route guard would catch them on the
    // next nav anyway, but this avoids briefly seeing a now-gated
    // admin page).
    if (auth.user?.requires_2fa === true) {
      await router.push({ name: 'account-2fa' })
    }
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="policy-page" data-density="operator">
    <span class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_twofa_policy.title') }}
    </span>

    <p class="fh-field-help intro">{{ t('admin_twofa_policy.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else class="policy-form" @submit.prevent="onSave">
      <div v-if="!isKvOverridden" class="fh-notice" data-tone="info">
        {{ t('admin_twofa_policy.kv_inheritance_note') }}
      </div>

      <fieldset class="role-fieldset">
        <legend class="fh-field-label">
          {{ t('admin_twofa_policy.roles_heading') }}
        </legend>
        <p class="fh-field-help">{{ t('admin_twofa_policy.roles_help') }}</p>
        <label v-for="r in ROLE_KEYS" :key="r.value" class="role-check">
          <input
            type="checkbox"
            :checked="requiredRoles.has(r.value)"
            @change="toggleRole(r.value)"
          />
          <span>{{ t(r.labelKey) }}</span>
        </label>
      </fieldset>

      <fieldset
        v-if="availableGroups.length > 0"
        class="role-fieldset"
      >
        <legend class="fh-field-label">
          {{ t('admin_twofa_policy.groups_heading') }}
        </legend>
        <p class="fh-field-help">{{ t('admin_twofa_policy.groups_help') }}</p>
        <ul class="group-checks">
          <li v-for="g in availableGroups" :key="g.id">
            <label class="group-check">
              <input
                type="checkbox"
                :checked="requiredGroups.some((x) => x.id === g.id)"
                @change="toggleGroup(g)"
              />
              <span class="group-name">{{ g.name }}</span>
              <span v-if="g.is_company_inbox" class="fh-pill">
                {{ t('admin_twofa_policy.groups_inbox') }}
              </span>
            </label>
          </li>
        </ul>
      </fieldset>

      <section class="effects">
        <h2 class="form-h2">{{ t('admin_twofa_policy.effects_heading') }}</h2>
        <ul>
          <li>{{ t('admin_twofa_policy.effect_redirect') }}</li>
          <li>{{ t('admin_twofa_policy.effect_recovery') }}</li>
          <li>{{ t('admin_twofa_policy.effect_lockout') }}</li>
        </ul>
      </section>

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

.role-fieldset {
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-3);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.role-check,
.group-check {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-2);
  cursor: pointer;
}

.group-checks {
  list-style: none;
  margin: var(--fh-space-1) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
}

.group-name {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
}

.effects ul {
  list-style: disc;
  padding-left: var(--fh-space-4);
  margin: var(--fh-space-2) 0 0;
  color: var(--fh-subtle);
  font-size: var(--fh-text-body-sm);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
}

.form-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.25rem;
  margin: 0;
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
}
</style>
