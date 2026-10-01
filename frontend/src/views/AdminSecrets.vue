<script setup lang="ts">
  /* Admin › Sharing › Secrets (v2.24.0): every secret's metadata - label,
   * sender, audience, views, expiry, state - with Burn now. There is no admin
   * route to a secret's content or its links, by design. A tab of
   * AdminTabShell, which renders the page header. */
  import { onMounted, ref, watch } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRouter } from 'vue-router'

  import { burnSecret, listAllSecrets } from '@/api/secrets'
  import Pager from '@/components/Pager.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
  import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
  import { useUiStore } from '@/stores/ui'
  import type { AdminSecretListItem, SecretRecipientSummary, SecretState } from '@/types/api'
  import { secretStatePill } from '@/utils/statePill'

  const { t } = useI18n()
  const { describe } = useApiError()
  const { formatDate, formatExpiry } = useSiteDateFormat()
  const ui = useUiStore()
  const router = useRouter()

  const items = ref<AdminSecretListItem[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = 50
  const q = ref('')
  const stateFilter = ref<'' | SecretState>('active')
  const loading = ref(true)
  const errorMsg = ref<string | null>(null)

  async function load() {
    loading.value = true
    errorMsg.value = null
    try {
      const { data } = await listAllSecrets({
        state: stateFilter.value ? [stateFilter.value] : [],
        q: q.value || undefined,
        page: page.value,
        page_size: pageSize,
      })
      items.value = data.items
      total.value = data.total ?? data.items.length
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      loading.value = false
    }
  }

  watch(stateFilter, () => {
    page.value = 1
    void load()
  })
  watch(page, () => void load())
  useDebouncedSearch(q, () => {
    page.value = 1
    void load()
  })

  function audience(sum: SecretRecipientSummary | null | undefined): string {
    if (!sum) return '-'
    const parts: string[] = []
    if (sum.users) parts.push(t('secrets.audience.users', { n: sum.users }, sum.users))
    if (sum.groups) parts.push(t('secrets.audience.groups', { n: sum.groups }, sum.groups))
    if (sum.emails) parts.push(t('secrets.audience.emails', { n: sum.emails }, sum.emails))
    if (sum.link) parts.push(t('secrets.audience.link'))
    return parts.join(' · ') || '-'
  }

  async function onBurn(s: AdminSecretListItem) {
    const ok = await ui.confirm({
      title: t('secrets.detail.burn_title'),
      message: t('admin_secrets.burn_confirm', { sender: s.sender.display_name }),
      confirmLabel: t('secrets.detail.burn'),
      danger: true,
    })
    if (!ok) return
    try {
      await burnSecret(s.id)
      ui.pushToast(t('secrets.detail.burned_toast'), 'success')
      await load()
    } catch (err) {
      ui.pushToast(describe(err), 'error')
    }
  }

  onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="fh-tab-toolbar">
      <span class="fh-mono total-count">{{ t('admin_secrets.total', { n: total }, total) }}</span>
    </div>
    <p class="fh-field-help intro">{{ t('admin_secrets.intro') }}</p>

    <div class="filters">
      <input
        v-model.trim="q"
        type="search"
        class="fh-field-input search"
        autocomplete="off"
        :aria-label="t('admin_secrets.search_placeholder')"
        :placeholder="t('admin_secrets.search_placeholder')"
      />
      <select v-model="stateFilter" class="filter-select" :aria-label="t('secrets.filter_label')">
        <option value="">{{ t('secrets.filter.all') }}</option>
        <option value="active">{{ t('secret_state.active') }}</option>
        <option value="burned">{{ t('secret_state.burned') }}</option>
        <option value="expired">{{ t('secret_state.expired') }}</option>
        <option value="revoked">{{ t('secret_state.revoked') }}</option>
      </select>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>
    <p v-else-if="!items.length" class="fh-field-help">{{ t('admin_secrets.empty') }}</p>

    <template v-else>
      <div class="fh-table-scroll">
        <table class="secrets-table">
          <thead>
            <tr>
              <th>{{ t('secrets.col.label') }}</th>
              <th>{{ t('secrets.col.from') }}</th>
              <th>{{ t('secrets.col.to') }}</th>
              <th>{{ t('secrets.col.views') }}</th>
              <th>{{ t('secrets.col.expires') }}</th>
              <th>{{ t('secrets.col.state') }}</th>
              <th>{{ t('admin_secrets.col.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in items" :key="s.id">
              <td>
                <div class="row-name">{{ s.label || t('secrets.no_label') }}</div>
                <div class="fh-mono row-hint">{{ formatDate(s.created_at) }}</div>
              </td>
              <td>
                <div class="row-name">{{ s.sender.display_name }}</div>
                <div class="fh-mono row-hint">{{ s.sender_email }}</div>
              </td>
              <td>{{ audience(s.recipient_summary) }}</td>
              <td class="fh-mono nowrap">
                {{
                  s.max_views == null
                    ? t('secrets.views.used_unlimited', { used: s.views_used })
                    : t('secrets.views.used_of', { used: s.views_used, max: s.max_views })
                }}
              </td>
              <td class="fh-mono">{{ formatExpiry(s.expires_at) }}</td>
              <td>
                <span class="fh-pill" :data-state="secretStatePill(s.state)">
                  {{ t(`secret_state.${s.state}`) }}
                </span>
              </td>
              <td>
                <div class="actions-cell">
                  <button
                    type="button"
                    class="fh-btn-text"
                    @click="router.push({ name: 'secret-detail', params: { id: s.id } })"
                  >
                    {{ t('admin_secrets.open') }}
                  </button>
                  <button
                    v-if="s.state === 'active'"
                    type="button"
                    class="fh-btn-text danger"
                    @click="onBurn(s)"
                  >
                    {{ t('secrets.detail.burn') }}
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <Pager v-model:page="page" :total="total" :page-size="pageSize" />
    </template>
  </div>
</template>

<style scoped>
  .intro {
    max-width: 64ch;
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: var(--fh-space-3);
    align-items: center;
    margin-bottom: var(--fh-space-3);
  }
  .search {
    flex: 1 1 220px;
    max-width: 320px;
  }
  .filter-select {
    font: inherit;
    background: transparent;
    border: var(--fh-border-strong);
    border-radius: var(--fh-radius-sm);
    padding: 4px 8px;
    color: var(--fh-ink);
  }
  .loading {
    color: var(--fh-subtle);
    padding: var(--fh-space-4) 0;
  }
  .secrets-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: var(--fh-space-3);
  }
  .secrets-table th,
  .secrets-table td {
    text-align: left;
    padding: var(--fh-space-2) var(--fh-space-3);
    border-bottom: 1px solid var(--fh-rule);
    vertical-align: top;
  }
  .secrets-table th {
    font-family: var(--fh-font-mono);
    font-size: var(--fh-text-mono-sm);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--fh-subtle);
    font-weight: 500;
  }
  .row-name {
    font-weight: 500;
  }
  .row-hint {
    font-size: var(--fh-text-mono-sm);
    color: var(--fh-subtle);
  }
  .actions-cell {
    display: flex;
    gap: var(--fh-space-3);
    white-space: nowrap;
  }
  .nowrap {
    white-space: nowrap;
  }
  .fh-btn-text.danger {
    color: var(--fh-danger);
  }
</style>
