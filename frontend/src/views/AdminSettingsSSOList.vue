<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { listProviders, type OIDCProviderItem } from '@/api/settings'
import { useApiError } from '@/composables/useApiError'

const { t } = useI18n()
const { describe } = useApiError()
const router = useRouter()

const items = ref<OIDCProviderItem[]>([])
const loading = ref(true)
const errorMsg = ref<string | null>(null)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await listProviders()
    items.value = data.items
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

function open(p: OIDCProviderItem) {
  router.push({ name: 'admin-settings-sso-edit', params: { id: p.id } })
}

function newProvider() {
  router.push({ name: 'admin-settings-sso-new' })
}

onMounted(load)
</script>

<template>
  <div class="sso-list" data-density="operator">
    <div class="header-row">
      <div>
        <h1 class="fh-eyebrow">{{ t('admin_settings.eyebrow') }} / {{ t('admin_sso_list.title') }}</h1>
      </div>
      <button type="button" class="fh-btn" @click="newProvider">
        + {{ t('admin_sso_list.add_button') }}
      </button>
    </div>

    <p class="fh-field-help intro">{{ t('admin_sso_list.intro') }}</p>

    <hr class="fh-rule" />

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div
v-else-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>

    <div v-else-if="items.length === 0" class="empty fh-notice" data-tone="muted">
      <strong>{{ t('admin_sso_list.empty_title') }}</strong>
      <p>{{ t('admin_sso_list.empty_body') }}</p>
    </div>

    <table v-else class="provider-table">
      <thead>
        <tr>
          <th>{{ t('admin_sso_list.col.name') }}</th>
          <th>{{ t('admin_sso_list.col.preset') }}</th>
          <th>{{ t('admin_sso_list.col.status') }}</th>
          <th>{{ t('admin_sso_list.col.users') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="p in items"
          :key="p.id"
          tabindex="0"
          @click="open(p)"
          @keydown.enter="open(p)"
        >
          <td class="name-cell">
            <strong>{{ p.name }}</strong>
            <code class="fh-mono issuer">{{ p.issuer_url }}</code>
          </td>
          <td>
            <span class="fh-pill" :data-state="p.preset">{{ p.preset }}</span>
          </td>
          <td>
            <span
              v-if="!p.client_secret_set"
              class="fh-pill"
              data-state="warning"
            >
              {{ t('admin_sso_list.status.no_secret') }}
            </span>
            <span
              v-else-if="p.enabled"
              class="fh-pill"
              data-state="active"
            >
              {{ t('admin_sso_list.status.enabled') }}
            </span>
            <span v-else class="fh-pill">
              {{ t('admin_sso_list.status.disabled') }}
            </span>
          </td>
          <td class="user-count">{{ p.user_count }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.sso-list {
  max-width: none;
}

.header-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--fh-space-3);
}

.intro {
  margin: var(--fh-space-2) 0 var(--fh-space-3);
  max-width: 64ch;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-4) 0;
}

.empty {
  margin-top: var(--fh-space-3);
}

.empty p {
  margin-top: var(--fh-space-1);
  color: var(--fh-ink-soft);
}

.provider-table {
  width: 100%;
  margin-top: var(--fh-space-3);
  border-collapse: collapse;
}

.provider-table th,
.provider-table td {
  text-align: left;
  padding: var(--fh-space-2) var(--fh-space-3);
  border-bottom: 1px solid var(--fh-rule);
}

.provider-table th {
  font-size: var(--fh-text-mono-sm);
  font-family: var(--fh-font-mono);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--fh-subtle);
  font-weight: 500;
}

.provider-table tbody tr {
  cursor: pointer;
  transition: background 120ms;
}

.provider-table tbody tr:hover {
  background: var(--fh-hover);
}

/* These rows are `tabindex="0"` and Enter navigates, so they need a real
   indicator. They used to set `outline: none` and rely on a background from an
   undefined custom property: focus moved through the table invisibly and Enter
   opened whichever row happened to have it (audit #2). Inset, because an
   outset ring on a table row is clipped by the neighbouring cells. */
.provider-table tbody tr:focus-visible {
  background: var(--fh-hover);
  outline: 2px solid var(--fh-focus-ring);
  outline-offset: -2px;
}

.name-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.issuer {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.user-count {
  font-variant-numeric: tabular-nums;
  font-family: var(--fh-font-mono);
}
</style>
