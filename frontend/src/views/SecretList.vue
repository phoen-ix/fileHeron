<script setup lang="ts">
  /* /secrets - the secrets sent to me, the ones I sent, and secret requests
   * (v2.24.0). Metadata only: a secret is opened on its own page, never from a
   * list. The Requests tab is its own component with its own filters. */
  import { computed, onMounted, ref, watch } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRoute, useRouter } from 'vue-router'

  import { listSecrets } from '@/api/secrets'
  import Pager from '@/components/Pager.vue'
  import SecretRequestList from '@/components/SecretRequestList.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
  import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
  import { useAuthStore } from '@/stores/auth'
  import type { SecretListItem, SecretRecipientSummary, SecretState } from '@/types/api'
  import { secretStatePill } from '@/utils/statePill'

  const { t } = useI18n()
  const { describe } = useApiError()
  const { formatDate, formatExpiry } = useSiteDateFormat()
  const route = useRoute()
  const router = useRouter()
  const auth = useAuthStore()

  type Box = 'received' | 'sent' | 'requests'
  const box = computed<Box>(() =>
    route.query.box === 'sent' ? 'sent' : route.query.box === 'requests' ? 'requests' : 'received',
  )
  const canSend = computed(() => auth.user?.can_send_secrets === true)

  type StateFilter = 'active' | 'ended' | 'all'
  const STATES: Record<StateFilter, SecretState[]> = {
    active: ['active'],
    ended: ['burned', 'expired', 'revoked'],
    all: [],
  }
  const stateFilter = ref<StateFilter>('active')
  const q = ref('')
  const page = ref(1)
  const pageSize = 50
  const items = ref<SecretListItem[]>([])
  const total = ref(0)
  const loading = ref(true)
  const errorMsg = ref<string | null>(null)

  async function load() {
    if (box.value === 'requests') return
    loading.value = true
    errorMsg.value = null
    try {
      const { data } = await listSecrets({
        box: box.value === 'sent' ? 'sent' : 'received',
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

  function setBox(next: Box) {
    if (next !== box.value) router.replace({ name: 'secrets', query: { box: next } })
  }

  watch(box, () => {
    page.value = 1
    void load()
  })
  watch(stateFilter, () => {
    page.value = 1
    void load()
  })
  watch(page, () => void load())
  useDebouncedSearch(q, () => {
    page.value = 1
    void load()
  })

  function open(s: SecretListItem) {
    router.push({ name: 'secret-detail', params: { id: s.id } })
  }

  function viewsCell(s: SecretListItem): string {
    if (box.value === 'received') {
      return s.my_views_left == null
        ? t('secrets.views.unlimited')
        : t('secrets.views.left', { n: s.my_views_left }, s.my_views_left)
    }
    return s.max_views == null
      ? t('secrets.views.used_unlimited', { used: s.views_used })
      : t('secrets.views.used_of', { used: s.views_used, max: s.max_views })
  }

  /** Who a received secret is from - the sender, or how an answer to my
   *  request came in when nobody signed in wrote it. */
  function fromText(s: SecretListItem): string {
    if (s.sender) return s.sender.display_name
    if (s.answered_via === 'email') return s.answered_by_email ?? ''
    return t('secrets.detail.via_request_link')
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

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <span class="fh-eyebrow">{{ t('secrets.eyebrow') }}</span>
        <h1 class="fh-display-md">{{ t('secrets.title') }}</h1>
      </div>
      <div v-if="canSend" class="header-actions">
        <RouterLink
          :to="{ name: 'secret-request-create' }"
          class="fh-btn-text"
          data-testid="request-secret"
        >
          {{ t('secret_requests.new') }}
        </RouterLink>
        <RouterLink :to="{ name: 'secret-create' }" class="fh-btn">
          {{ t('secrets.new') }} <span aria-hidden="true">→</span>
        </RouterLink>
      </div>
    </div>
    <p class="fh-field-help intro">{{ t('secrets.intro') }}</p>

    <hr class="fh-rule" />

    <div class="filters">
      <div class="box-toggle" role="group" :aria-label="t('secrets.box_label')">
        <button
          type="button"
          class="box-btn"
          :aria-pressed="box === 'received'"
          data-testid="box-received"
          @click="setBox('received')"
        >
          {{ t('secrets.box.received') }}
        </button>
        <button
          type="button"
          class="box-btn"
          :aria-pressed="box === 'sent'"
          data-testid="box-sent"
          @click="setBox('sent')"
        >
          {{ t('secrets.box.sent') }}
        </button>
        <button
          type="button"
          class="box-btn"
          :aria-pressed="box === 'requests'"
          data-testid="box-requests"
          @click="setBox('requests')"
        >
          {{ t('secrets.box.requests') }}
        </button>
      </div>
      <template v-if="box !== 'requests'">
        <input
          v-model.trim="q"
          type="search"
          class="fh-field-input search"
          autocomplete="off"
          :aria-label="t('secrets.search_placeholder')"
          :placeholder="t('secrets.search_placeholder')"
        />
        <select v-model="stateFilter" class="filter-select" :aria-label="t('secrets.filter_label')">
          <option value="active">{{ t('secrets.filter.active') }}</option>
          <option value="ended">{{ t('secrets.filter.ended') }}</option>
          <option value="all">{{ t('secrets.filter.all') }}</option>
        </select>
      </template>
    </div>

    <SecretRequestList v-if="box === 'requests'" :can-request="canSend" />

    <div v-else-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="items.length > 0">
      <div class="fh-table-scroll">
        <table class="secret-table">
          <thead>
            <tr>
              <th>{{ t('secrets.col.label') }}</th>
              <th>{{ box === 'received' ? t('secrets.col.from') : t('secrets.col.to') }}</th>
              <th>{{ t('secrets.col.views') }}</th>
              <th>{{ t('secrets.col.expires') }}</th>
              <th>{{ t('secrets.col.state') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="s in items"
              :key="s.id"
              tabindex="0"
              @click="open(s)"
              @keydown.enter="open(s)"
            >
              <td>
                <div class="row-name">
                  {{ s.label || t('secrets.no_label') }}
                  <span v-if="s.has_passphrase" class="fh-mono row-flag">{{
                    t('secrets.passphrase_flag')
                  }}</span>
                </div>
                <div class="fh-mono row-hint">
                  {{ t('secrets.created', { d: formatDate(s.created_at) }) }}
                </div>
              </td>
              <td>
                <template v-if="box === 'received'">{{ fromText(s) }}</template>
                <template v-else>{{ audience(s.recipient_summary) }}</template>
              </td>
              <td class="fh-mono">{{ viewsCell(s) }}</td>
              <td class="fh-mono">{{ formatExpiry(s.expires_at) }}</td>
              <td>
                <span class="fh-pill" :data-state="secretStatePill(s.state)">
                  {{ t(`secret_state.${s.state}`) }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <Pager v-model:page="page" :total="total" :page-size="pageSize" />
    </template>

    <div v-else class="empty-state">
      <p class="fh-display-md empty-display">{{ t(`secrets.empty.${box}`) }}</p>
      <RouterLink v-if="canSend" :to="{ name: 'secret-create' }" class="fh-btn">
        {{ t('secrets.new') }} <span aria-hidden="true">→</span>
      </RouterLink>
    </div>
  </div>
</template>

<style scoped>
  .header-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: var(--fh-space-4);
  }

  .intro {
    max-width: 64ch;
  }

  .header-actions {
    display: flex;
    gap: var(--fh-space-4);
    align-items: center;
  }

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

  .secret-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: var(--fh-space-3);
  }

  .secret-table th,
  .secret-table td {
    text-align: left;
    padding: var(--fh-space-2) var(--fh-space-3);
    border-bottom: 1px solid var(--fh-rule);
    vertical-align: top;
  }

  .secret-table th {
    font-family: var(--fh-font-mono);
    font-size: var(--fh-text-mono-sm);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--fh-subtle);
    font-weight: 500;
  }

  .secret-table tbody tr {
    cursor: pointer;
  }

  .secret-table tbody tr:hover {
    background: var(--fh-hover);
  }

  .secret-table tbody tr:focus-visible {
    background: var(--fh-hover);
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
