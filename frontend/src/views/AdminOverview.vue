<!-- /admin lands here (it used to redirect to Users). Three things, in the
     order an admin needs them: what needs attention right now, a search over
     every admin page, tab, section and field ("where is the cooldown
     setting?"), and every page as category cards. The cards render from
     ADMIN_NAV, so they cannot drift from the sidebar; the search index is the
     static registry in config/adminSearchIndex.ts, pinned by its own tests.

     The search box is deliberately NOT `type="search"` and carries no
     "search" in its placeholder: useKeyboardShortcuts' `/` focuses the first
     such input in DOM order, and on every other admin page that must stay the
     page's own filter box. -->
<template>
  <div class="fh-page admin-overview" data-density="operator">
    <AdminPageHeader>
      <p class="fh-field-help intro">{{ t('admin_overview.intro') }}</p>
    </AdminPageHeader>

    <div class="ov-search">
      <label class="fh-sr-only" for="ov-search-input">{{ t('admin_overview.search_label') }}</label>
      <svg class="ov-search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
      </svg>
      <input
        id="ov-search-input"
        v-model="query"
        type="text"
        class="ov-search-input"
        role="combobox"
        aria-autocomplete="list"
        :aria-expanded="showResults"
        aria-controls="ov-search-list"
        :aria-activedescendant="showResults && results[active] ? optionId(active) : undefined"
        :placeholder="t('admin_overview.search_placeholder')"
        autocomplete="off"
        @focus="open = true"
        @blur="onBlur"
        @keydown="onKeydown"
      />
      <ul
        v-show="showResults"
        id="ov-search-list"
        role="listbox"
        class="ov-results"
        :aria-label="t('admin_overview.search_results')"
      >
        <li
          v-for="(r, i) in results"
          :id="optionId(i)"
          :key="r.key"
          role="option"
          :aria-selected="i === active"
          class="ov-result"
          :class="{ 'is-active': i === active }"
          @mousedown.prevent="go(r)"
          @mousemove="active = i"
        >
          <span class="ov-result-label">{{ r.label }}</span>
          <span class="ov-result-crumb">{{ r.crumb }}</span>
        </li>
        <li v-if="!results.length" class="ov-results-empty" aria-disabled="true">
          {{ t('admin_overview.search_empty') }}
        </li>
      </ul>
    </div>

    <section class="ov-section" aria-labelledby="ov-attention-h">
      <h2 id="ov-attention-h" class="ov-h2">{{ t('admin_overview.attention_heading') }}</h2>
      <div class="ov-tiles">
        <RouterLink
          v-for="tile in visibleTiles"
          :key="tile.key"
          :to="tile.to"
          class="ov-tile"
          :data-state="tile.warn && typeof tile.count === 'number' && tile.count > 0 ? 'warn' : undefined"
        >
          <b class="ov-tile-n" :title="tile.count === null ? t('admin_overview.unavailable') : undefined">{{
            tile.count === undefined ? '…' : tile.count === null ? '–' : tile.count
          }}</b>
          <span class="ov-tile-l">{{ t(tile.labelKey) }}</span>
        </RouterLink>
      </div>
    </section>

    <section class="ov-section" aria-labelledby="ov-status-h">
      <h2 id="ov-status-h" class="ov-h2">{{ t('admin_overview.health_heading') }}</h2>
      <div class="ov-health">
        <RouterLink :to="{ name: 'admin-system' }" class="ov-health-item">
          <span class="dot" :class="status?.version.update_available ? 'warn' : 'ok'"></span>
          <template v-if="status">
            {{ t('admin_overview.running', { v: status.version.running }) }}
            <span class="ov-health-sub">
              {{
                status.version.update_available && status.version.latest
                  ? t('admin_overview.update_available', { v: status.version.latest })
                  : t('admin_overview.up_to_date')
              }}
            </span>
          </template>
          <template v-else>{{ statusFailed ? t('admin_overview.unavailable') : '…' }}</template>
        </RouterLink>
        <span v-for="c in checks" :key="c.key" class="ov-health-item">
          <span class="dot" :class="c.ok === null ? 'unknown' : c.ok ? 'ok' : 'warn'"></span>{{ t(c.labelKey) }}
        </span>
      </div>
    </section>

    <section class="ov-section" aria-labelledby="ov-pages-h">
      <h2 id="ov-pages-h" class="ov-h2">{{ t('admin_overview.pages_heading') }}</h2>
      <div class="ov-cats">
        <div v-for="cat in ADMIN_NAV" :key="cat.key" class="ov-cat">
          <h3 class="ov-cat-h">{{ t(cat.labelKey) }}</h3>
          <ul class="ov-cat-list">
            <li v-for="item in cat.items" :key="item.routeName">
              <RouterLink :to="{ name: item.routeName }">{{ t(item.labelKey) }}</RouterLink>
              <span v-if="item.tabs" class="ov-cat-tabs">
                {{ item.tabs.map((tab) => t(tab.labelKey)).join(' · ') }}
              </span>
            </li>
          </ul>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { type RouteLocationRaw, useRouter } from 'vue-router'

import {
  adminListFiles,
  getInboxUnreadCount,
  getSystemStatus,
  listIpBlocks,
  type SystemStatusResponse,
} from '@/api/admin'
import { listPendingApprovals } from '@/api/shares'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import { ADMIN_NAV, findNavItem } from '@/config/adminNav'
import { ADMIN_SEARCH_INDEX } from '@/config/adminSearchIndex'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()

/* ---- attention tiles -------------------------------------------------- */

interface Tile {
  key: string
  labelKey: string
  to: RouteLocationRaw
  /** undefined = loading, null = the request failed, number = the figure. */
  count: number | null | undefined
  /** Colour the figure when it is above zero. */
  warn: boolean
  /** Only shown for admins who can approve. */
  requiresApprover?: boolean
}

const tiles = ref<Tile[]>([
  { key: 'inbox', labelKey: 'admin_overview.tile_unread_inbox', to: { name: 'admin-inbox' }, count: undefined, warn: true },
  { key: 'quarantine', labelKey: 'admin_overview.tile_quarantined', to: { name: 'admin-quarantine' }, count: undefined, warn: true },
  { key: 'blocks', labelKey: 'admin_overview.tile_live_blocks', to: { name: 'admin-ip-blocks' }, count: undefined, warn: false },
  { key: 'failed', labelKey: 'admin_overview.tile_failed_tasks', to: { name: 'admin-scheduled-tasks' }, count: undefined, warn: true },
  { key: 'approvals', labelKey: 'admin_overview.tile_pending_approvals', to: { name: 'approvals' }, count: undefined, warn: false, requiresApprover: true },
])

const visibleTiles = computed(() =>
  tiles.value.filter((tile) => !tile.requiresApprover || auth.user?.can_approve_shares),
)

function setCount(key: string, count: number | null) {
  const tile = tiles.value.find((x) => x.key === key)
  if (tile) tile.count = count
}

/** Every figure loads on its own; one failing endpoint blanks one tile. */
async function loadTile(key: string, fetch: () => Promise<number>) {
  try {
    setCount(key, await fetch())
  } catch {
    setCount(key, null)
  }
}

/* ---- status strip ------------------------------------------------------ */

const status = ref<SystemStatusResponse | null>(null)
const statusFailed = ref(false)

const checks = computed(() => {
  const live = status.value?.live
  const ok = (s: string | undefined) => (s === undefined ? null : s === 'ok')
  return [
    { key: 'db', labelKey: 'admin_overview.check_db', ok: ok(live?.db.status) },
    { key: 'redis', labelKey: 'admin_overview.check_redis', ok: ok(live?.redis.status) },
    { key: 'av', labelKey: 'admin_overview.check_av', ok: ok(live?.av.status) },
  ]
})

onMounted(() => {
  void loadTile('inbox', async () => (await getInboxUnreadCount()).data.unread)
  void loadTile('quarantine', async () => (await adminListFiles({ state: 'infected', page_size: 1 })).data.total)
  void loadTile('blocks', async () => (await listIpBlocks({ status: 'active', page_size: 1 })).data.total)
  if (auth.user?.can_approve_shares) {
    void loadTile('approvals', async () => (await listPendingApprovals({ page_size: 1 })).data.total)
  }
  void (async () => {
    try {
      const { data } = await getSystemStatus()
      status.value = data
      setCount('failed', data.crons.reduce((n, c) => n + c.last_24h.failure, 0))
    } catch {
      statusFailed.value = true
      setCount('failed', null)
    }
  })()
})

/* ---- find a setting ---------------------------------------------------- */

interface Hit {
  key: string
  label: string
  crumb: string
  to: RouteLocationRaw
  score: number
}

const query = ref('')
const open = ref(false)
const active = ref(0)

function crumbFor(routeName: string): string {
  const m = findNavItem(routeName)
  if (!m) return ''
  const parts = [m.category ? t(m.category.labelKey) : '', t(m.item.labelKey)]
  if (m.tab) parts.push(t(m.tab.labelKey))
  return parts.filter(Boolean).join(' › ')
}

const results = computed<Hit[]>(() => {
  const q = query.value.trim().toLowerCase()
  if (q.length < 2) return []
  const words = q.split(/\s+/)
  const hits: Hit[] = []
  const consider = (
    key: string,
    label: string,
    routeName: string,
    hash: string | undefined,
    keywords: readonly string[],
    weight: number,
  ) => {
    const crumb = crumbFor(routeName)
    const hay = `${label} ${crumb} ${keywords.join(' ')}`.toLowerCase()
    if (!words.every((w) => hay.includes(w))) return
    const l = label.toLowerCase()
    let score = weight
    if (l.startsWith(q)) score += 30
    else if (l.includes(q)) score += 20
    else if (keywords.some((k) => k.includes(q))) score += 12
    else score += 4
    hits.push({ key, label, crumb, to: { name: routeName, hash }, score })
  }
  for (const cat of ADMIN_NAV) {
    for (const item of cat.items) {
      consider(`page:${item.routeName}`, t(item.labelKey), item.routeName, undefined, [], 8)
      for (const tab of item.tabs ?? []) {
        consider(`tab:${tab.routeName}`, t(tab.labelKey), tab.routeName, undefined, [], 6)
      }
    }
  }
  ADMIN_SEARCH_INDEX.forEach((e, i) =>
    consider(`s:${i}`, t(e.labelKey), e.routeName, e.hash, e.keywords ?? [], 2),
  )
  hits.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label))
  return hits.slice(0, 8)
})

const showResults = computed(() => open.value && query.value.trim().length >= 2)

function optionId(i: number): string {
  return `ov-search-option-${i}`
}

function go(hit: Hit) {
  open.value = false
  void router.push(hit.to)
}

function onKeydown(e: KeyboardEvent) {
  if (!showResults.value) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    active.value = Math.min(results.value.length - 1, active.value + 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    active.value = Math.max(0, active.value - 1)
  } else if (e.key === 'Enter') {
    const hit = results.value[active.value]
    if (hit) {
      e.preventDefault()
      go(hit)
    }
  } else if (e.key === 'Escape') {
    open.value = false
  }
}

function onBlur() {
  // Let a mousedown on an option run first (it is prevented, so focus stays).
  window.setTimeout(() => {
    open.value = false
  }, 120)
}
</script>

<style scoped>
.intro {
  max-width: 64ch;
  margin: var(--fh-space-1) 0 0;
}

.ov-search {
  position: relative;
  max-width: 34rem;
  margin: var(--fh-space-3) 0 var(--fh-space-5);
}

.ov-search-icon {
  position: absolute;
  left: 12px;
  top: 13px;
  color: var(--fh-subtle);
  pointer-events: none;
}

.ov-search-input {
  width: 100%;
  font: inherit;
  font-size: var(--fh-text-body-md);
  color: var(--fh-ink);
  background: var(--fh-paper-raised);
  border: 1px solid var(--fh-hairline-strong);
  border-radius: var(--fh-radius-sm);
  padding: 10px 12px 10px 36px;
}

.ov-search-input::placeholder {
  color: var(--fh-subtle);
}

.ov-search-input:focus {
  outline: none;
  border-color: var(--fh-accent);
}

.ov-results {
  position: absolute;
  z-index: 5;
  left: 0;
  right: 0;
  top: calc(100% + 4px);
  margin: 0;
  padding: 4px 0;
  list-style: none;
  background: var(--fh-paper-raised);
  border: 1px solid var(--fh-hairline-strong);
  box-shadow: 0 4px 32px rgba(26, 29, 36, 0.06);
  max-height: 22rem;
  overflow: auto;
}

.ov-result {
  display: grid;
  gap: 2px;
  padding: 8px 12px;
  cursor: pointer;
}

.ov-result.is-active {
  background: var(--fh-paper-sunk);
}

.ov-result-label {
  font-size: var(--fh-text-body-sm);
  color: var(--fh-ink);
}

.ov-result-crumb {
  font-family: var(--fh-font-mono);
  font-size: 11px;
  color: var(--fh-subtle);
  letter-spacing: 0.02em;
}

.ov-results-empty {
  padding: 8px 12px;
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
}

.ov-section {
  margin-bottom: var(--fh-space-5);
}

.ov-h2 {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--fh-subtle);
  margin: 0 0 var(--fh-space-3);
}

.ov-tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 1px;
  background: var(--fh-hairline);
  border: 1px solid var(--fh-hairline);
}

.ov-tile {
  display: grid;
  gap: 2px;
  padding: var(--fh-space-3);
  background: var(--fh-paper-raised);
  color: var(--fh-ink);
  text-decoration: none;
}

.ov-tile:hover {
  background: var(--fh-paper-sunk);
  color: var(--fh-ink);
}

.ov-tile-n {
  font-family: var(--fh-font-display);
  font-weight: 400;
  font-size: 1.75rem;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.ov-tile[data-state='warn'] .ov-tile-n {
  color: var(--fh-accent);
}

.ov-tile-l {
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
}

.ov-health {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-2) var(--fh-space-4);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-ink-soft);
}

.ov-health-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: inherit;
  text-decoration: none;
}

.ov-health-sub {
  color: var(--fh-subtle);
}

.dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--fh-hairline-strong);
}

.dot.ok {
  background: var(--fh-success);
}

.dot.warn {
  background: var(--fh-accent);
}

.ov-cats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: var(--fh-space-4) var(--fh-space-5);
}

.ov-cat-h {
  font-family: var(--fh-font-mono);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--fh-subtle);
  margin: 0 0 var(--fh-space-2);
  padding-bottom: var(--fh-space-1);
  border-bottom: 1px solid var(--fh-hairline);
}

.ov-cat-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 4px;
}

.ov-cat-list a {
  color: var(--fh-ink-soft);
  text-decoration: none;
  font-size: var(--fh-text-body-sm);
}

.ov-cat-list a:hover {
  color: var(--fh-accent);
}

.ov-cat-tabs {
  display: block;
  font-family: var(--fh-font-mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  color: var(--fh-subtle);
}
</style>
