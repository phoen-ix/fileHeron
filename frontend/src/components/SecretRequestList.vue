<!-- The Requests tab of /secrets (v2.24.0): requests I made, and requests
     made of me. Metadata only; a request is answered or followed on its own
     page. -->
<template>
  <div class="request-list">
    <div class="filters">
      <div class="box-toggle" role="group" :aria-label="t('secret_requests.box_label')">
        <button
          type="button"
          class="box-btn"
          :aria-pressed="box === 'mine'"
          data-testid="requests-mine"
          @click="box = 'mine'"
        >
          {{ t('secret_requests.box.mine') }}
        </button>
        <button
          type="button"
          class="box-btn"
          :aria-pressed="box === 'asked'"
          data-testid="requests-asked"
          @click="box = 'asked'"
        >
          {{ t('secret_requests.box.asked') }}
        </button>
      </div>
      <input
        v-model.trim="q"
        type="search"
        class="fh-field-input search"
        autocomplete="off"
        :aria-label="t('secret_requests.search_placeholder')"
        :placeholder="t('secret_requests.search_placeholder')"
      />
      <select
        v-model="stateFilter"
        class="filter-select"
        :aria-label="t('secret_requests.filter_label')"
      >
        <option value="open">{{ t('secret_requests.filter.open') }}</option>
        <option value="closed">{{ t('secret_requests.filter.closed') }}</option>
        <option value="all">{{ t('secret_requests.filter.all') }}</option>
      </select>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="items.length > 0">
      <div class="fh-table-scroll">
        <table class="request-table">
          <thead>
            <tr>
              <th>{{ t('secret_requests.col.label') }}</th>
              <th>{{ box === 'mine' ? t('secret_requests.col.asked') : t('secrets.col.from') }}</th>
              <th>{{ t('secret_requests.col.open_until') }}</th>
              <th>{{ t('secrets.col.state') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="r in items"
              :key="r.id"
              tabindex="0"
              @click="open(r)"
              @keydown.enter="open(r)"
            >
              <td>
                <div class="row-name">
                  {{ r.label }}
                  <span v-if="r.has_passphrase" class="fh-mono row-flag">{{
                    t('secrets.passphrase_flag')
                  }}</span>
                </div>
                <div class="fh-mono row-hint">
                  {{ t('secret_requests.asked_on', { d: formatDate(r.created_at) }) }}
                </div>
              </td>
              <td>
                <template v-if="box === 'mine'">{{ audience(r.target_summary) }}</template>
                <template v-else>{{ r.requester.display_name }}</template>
              </td>
              <td class="fh-mono nowrap">{{ formatExpiry(r.expires_at) }}</td>
              <td>
                <span class="fh-pill" :data-state="secretRequestPill(secretRequestStateKey(r))">
                  {{ t(`secret_request_state.${secretRequestStateKey(r)}`) }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <Pager v-model:page="page" :total="total" :page-size="pageSize" />
    </template>

    <div v-else class="empty-state">
      <p class="fh-display-md empty-display">{{ t(`secret_requests.empty.${box}`) }}</p>
      <RouterLink
        v-if="canRequest && box === 'mine'"
        :to="{ name: 'secret-request-create' }"
        class="fh-btn"
      >
        {{ t('secret_requests.new') }} <span aria-hidden="true">→</span>
      </RouterLink>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, ref, watch } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRouter } from 'vue-router'

  import { listSecretRequests } from '@/api/secretRequests'
  import Pager from '@/components/Pager.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
  import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
  import type {
    SecretRecipientSummary,
    SecretRequestListItem,
    SecretRequestState,
  } from '@/types/api'
  import { secretRequestPill, secretRequestStateKey } from '@/utils/statePill'

  defineProps<{ canRequest: boolean }>()

  const { t } = useI18n()
  const { describe } = useApiError()
  const { formatDate, formatExpiry } = useSiteDateFormat()
  const router = useRouter()

  type StateFilter = 'open' | 'closed' | 'all'
  const STATES: Record<StateFilter, SecretRequestState[]> = {
    open: ['open'],
    closed: ['fulfilled', 'cancelled', 'expired'],
    all: [],
  }
  const box = ref<'mine' | 'asked'>('mine')
  const stateFilter = ref<StateFilter>('open')
  const q = ref('')
  const page = ref(1)
  const pageSize = 50
  const items = ref<SecretRequestListItem[]>([])
  const total = ref(0)
  const loading = ref(true)
  const errorMsg = ref<string | null>(null)

  async function load() {
    loading.value = true
    errorMsg.value = null
    try {
      const { data } = await listSecretRequests({
        box: box.value,
        state: STATES[stateFilter.value],
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

  watch([box, stateFilter], () => {
    page.value = 1
    void load()
  })
  watch(page, () => void load())
  useDebouncedSearch(q, () => {
    page.value = 1
    void load()
  })

  function open(r: SecretRequestListItem) {
    router.push({ name: 'secret-request-detail', params: { id: r.id } })
  }

  function audience(sum: SecretRecipientSummary | null | undefined): string {
    if (!sum) return '-'
    const parts: string[] = []
    if (sum.users) parts.push(t('secrets.audience.users', { n: sum.users }, sum.users))
    if (sum.groups) parts.push(t('secrets.audience.groups', { n: sum.groups }, sum.groups))
    if (sum.emails) parts.push(t('secrets.audience.emails', { n: sum.emails }, sum.emails))
    if (sum.link) parts.push(t('secrets.audience.link'))
    return parts.join(' · ') || '-'
  }

  onMounted(load)
</script>

<style scoped>
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: var(--fh-space-3);
    align-items: center;
    margin-bottom: var(--fh-space-4);
  }

  .box-toggle {
    display: inline-flex;
    border: var(--fh-border-strong);
    border-radius: var(--fh-radius-sm);
    overflow: hidden;
  }

  .box-btn {
    font: inherit;
    background: transparent;
    border: none;
    padding: 4px 12px;
    cursor: pointer;
    color: var(--fh-ink-soft);
  }

  .box-btn[aria-pressed='true'] {
    background: var(--fh-ink);
    color: var(--fh-paper);
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

  .request-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: var(--fh-space-3);
  }

  .request-table th,
  .request-table td {
    text-align: left;
    padding: var(--fh-space-2) var(--fh-space-3);
    border-bottom: 1px solid var(--fh-rule);
    vertical-align: top;
  }

  .request-table th {
    font-family: var(--fh-font-mono);
    font-size: var(--fh-text-mono-sm);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--fh-subtle);
    font-weight: 500;
  }

  .request-table tbody tr {
    cursor: pointer;
  }

  .request-table tbody tr:hover,
  .request-table tbody tr:focus-visible {
    background: var(--fh-hover);
  }

  .request-table tbody tr:focus-visible {
    outline: 2px solid var(--fh-focus-ring);
    outline-offset: -2px;
  }

  .row-name {
    font-weight: 500;
  }

  .row-flag {
    margin-left: var(--fh-space-2);
    font-size: var(--fh-text-mono-sm);
    color: var(--fh-subtle);
    font-weight: 400;
  }

  .row-hint {
    font-size: var(--fh-text-mono-sm);
    color: var(--fh-subtle);
  }

  .nowrap {
    white-space: nowrap;
  }

  .loading {
    color: var(--fh-subtle);
    padding: var(--fh-space-5) 0;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-2);
    align-items: flex-start;
    padding: var(--fh-space-5) 0;
  }

  .empty-display {
    margin: 0;
  }
</style>
