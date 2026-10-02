<script setup lang="ts">
  /* Admin › Sharing › Secrets › Requests (v2.24.0): every secret request's
   * metadata - what was asked, by whom, of whom, until when, its state - with
   * Cancel. No admin route reads a request's links or an answer's text. A tab
   * of AdminTabShell, which renders the page header. */
  import { onMounted, ref, watch } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { adminCancelSecretRequest, listAllSecretRequests } from '@/api/secretRequests'
  import Pager from '@/components/Pager.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
  import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
  import { useUiStore } from '@/stores/ui'
  import type {
    AdminSecretRequestListItem,
    SecretRecipientSummary,
    SecretRequestState,
  } from '@/types/api'
  import { secretRequestPill, secretRequestStateKey } from '@/utils/statePill'

  const { t } = useI18n()
  const { describe } = useApiError()
  const { formatDate, formatExpiry } = useSiteDateFormat()
  const ui = useUiStore()

  const items = ref<AdminSecretRequestListItem[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = 50
  const q = ref('')
  const stateFilter = ref<'' | SecretRequestState>('open')
  const loading = ref(true)
  const errorMsg = ref<string | null>(null)

  async function load() {
    loading.value = true
    errorMsg.value = null
    try {
      const { data } = await listAllSecretRequests({
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

  async function onCancel(r: AdminSecretRequestListItem) {
    const ok = await ui.confirm({
      title: t('secret_requests.detail.cancel_title'),
      message: t('admin_secret_requests.cancel_confirm', { name: r.requester.display_name }),
      confirmLabel: t('secret_requests.detail.cancel'),
      danger: true,
    })
    if (!ok) return
    try {
      await adminCancelSecretRequest(r.id)
      ui.pushToast(t('secret_requests.detail.cancelled_toast'), 'success')
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
      <span class="fh-mono total-count">{{
        t('admin_secret_requests.total', { n: total }, total)
      }}</span>
    </div>
    <p class="fh-field-help intro">{{ t('admin_secret_requests.intro') }}</p>

    <div class="filters">
      <input
        v-model.trim="q"
        type="search"
        class="fh-field-input search"
        autocomplete="off"
        :aria-label="t('admin_secret_requests.search_placeholder')"
        :placeholder="t('admin_secret_requests.search_placeholder')"
      />
      <select
        v-model="stateFilter"
        class="filter-select"
        :aria-label="t('secret_requests.filter_label')"
      >
        <option value="">{{ t('secret_requests.filter.all') }}</option>
        <option value="open">{{ t('secret_request_state.open') }}</option>
        <option value="fulfilled">{{ t('secret_request_state.fulfilled') }}</option>
        <option value="cancelled">{{ t('secret_request_state.cancelled') }}</option>
        <option value="expired">{{ t('secret_request_state.expired') }}</option>
      </select>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>
    <p v-else-if="!items.length" class="fh-field-help">{{ t('admin_secret_requests.empty') }}</p>

    <template v-else>
      <div class="fh-table-scroll">
        <table class="requests-table">
          <thead>
            <tr>
              <th>{{ t('secret_requests.col.label') }}</th>
              <th>{{ t('admin_secret_requests.col.requester') }}</th>
              <th>{{ t('secret_requests.col.asked') }}</th>
              <th>{{ t('secret_requests.col.open_until') }}</th>
              <th>{{ t('secrets.col.state') }}</th>
              <th>{{ t('admin_secrets.col.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in items" :key="r.id">
              <td>
                <div class="row-name">{{ r.label }}</div>
                <div class="fh-mono row-hint">{{ formatDate(r.created_at) }}</div>
              </td>
              <td>
                <div class="row-name">{{ r.requester.display_name }}</div>
                <div class="fh-mono row-hint">{{ r.requester_email }}</div>
              </td>
              <td>{{ audience(r.target_summary) }}</td>
              <td class="fh-mono nowrap">{{ formatExpiry(r.expires_at) }}</td>
              <td>
                <span class="fh-pill" :data-state="secretRequestPill(secretRequestStateKey(r))">
                  {{ t(`secret_request_state.${secretRequestStateKey(r)}`) }}
                </span>
              </td>
              <td>
                <div class="actions-cell">
                  <RouterLink
                    :to="{ name: 'secret-request-detail', params: { id: r.id } }"
                    class="fh-btn-text"
                  >
                    {{ t('admin_secrets.open') }}
                  </RouterLink>
                  <button
                    v-if="r.closed_reason === null"
                    type="button"
                    class="fh-btn-text danger"
                    @click="onCancel(r)"
                  >
                    {{ t('secret_requests.detail.cancel') }}
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
  .requests-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: var(--fh-space-3);
  }
  .requests-table th,
  .requests-table td {
    text-align: left;
    padding: var(--fh-space-2) var(--fh-space-3);
    border-bottom: 1px solid var(--fh-rule);
    vertical-align: top;
  }
  .requests-table th {
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
