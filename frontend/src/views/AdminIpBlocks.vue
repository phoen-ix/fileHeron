<script setup lang="ts">
/* Blocked sources: what the scan guard is currently refusing, and how to undo
 * it. The guard's POLICY (signals, thresholds, notifications) stays on
 * AdminSettingsScanGuard.vue - this page owns the STATE.
 *
 * They were one page, and that was the problem: the allowlist was a free-text
 * textarea on the settings form, so every save carried a whole-CSV snapshot and
 * an admin who allowlisted an address here, with the settings page open in
 * another tab, lost it on their next save. The allowlist below writes one entry
 * at a time through endpoints that serialise on a row lock.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  addScanGuardAllowlistEntry,
  allowIpBlock,
  createIpBlock,
  getScanGuardAllowlist,
  getScanGuardWatchlist,
  listIpBlocks,
  releaseAllIpBlocks,
  releaseIpBlock,
  removeScanGuardAllowlistEntry,
  type IpBlockListParams,
  type IpBlockRow,
  type IpBlockStatus,
  type WatchRow,
} from '@/api/admin'
import Pager from '@/components/Pager.vue'
import { useApiError } from '@/composables/useApiError'
import { useDebouncedSearch } from '@/composables/useDebouncedSearch'
import { usePaginatedList } from '@/composables/usePaginatedList'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import { parseServerDate } from '@/utils/datetime'

const { t } = useI18n()
const { describe } = useApiError()
const { formatDate } = useSiteDateFormat()
const ui = useUiStore()

// --- filters ---------------------------------------------------------------
const status = ref<IpBlockStatus>('active')
const reason = ref('')
const source = ref('')
const networkOnly = ref(false)
const search = ref('')

const REASONS = ['probe_path', 'api_404', 'auth_failure', 'network', 'manual'] as const
const STATUSES: IpBlockStatus[] = ['active', 'released', 'expired', 'all']

/** How many rows the CURRENT filters would match if the status filter were
 *  lifted. Only ever non-zero when the page came back empty, and only used to
 *  offer a way out of it. */
const historyTotal = ref(0)

const { items, total, page, pageSize, loading, errorMsg, load } =
  usePaginatedList<IpBlockRow>(async ({ page: p, pageSize: ps }) => {
    const filters = {
      reason: reason.value || undefined,
      source: (source.value || undefined) as 'auto' | 'manual' | undefined,
      is_network: networkOnly.value ? true : undefined,
      q: search.value.trim() || undefined,
    }
    const { data } = await listIpBlocks({
      ...filters,
      status: status.value,
      page: p,
      page_size: ps,
    })
    historyTotal.value =
      data.items.length === 0 && status.value !== 'all'
        ? await countOutsideStatusFilter(filters)
        : 0
    return { items: data.items, total: data.total }
  })

/** The page defaults to "in force", which is the right first answer for a
 *  page about enforcement - but on an instance whose blocks have all expired or
 *  been released it renders a bare "no blocks match these filters" over a table
 *  that does have history in it. That reads as data loss. Counting what the
 *  filter is hiding is what lets the empty state offer a way through.
 *
 *  Failures are swallowed to 0 on purpose: `usePaginatedList` turns anything the
 *  fetcher throws into `errorMsg`, so letting this propagate would replace a
 *  merely-empty list with a red error box. A missing hint is the right way to
 *  lose. */
async function countOutsideStatusFilter(
  filters: Omit<IpBlockListParams, 'status' | 'page' | 'page_size'>,
): Promise<number> {
  try {
    const { data } = await listIpBlocks({
      ...filters,
      status: 'all',
      page: 1,
      page_size: 1,
    })
    return data.total
  } catch {
    return 0
  }
}

function refilter() {
  // `page` has a watcher, so assigning 1 from any other page already reloads.
  // Doing both fired two identical requests; harmless thanks to
  // usePaginatedList's sequence token, but two round trips per keystroke.
  if (page.value !== 1) page.value = 1
  else void load()
}
useDebouncedSearch(search, refilter)
watch([status, reason, source, networkOnly], refilter)
watch(page, () => void load())

// --- allowlist -------------------------------------------------------------
const allowEntries = ref<string[]>([])
const allowInvalid = ref<string[]>([])
const newAllowEntry = ref('')
const allowBusy = ref(false)

async function loadAllowlist() {
  try {
    const { data } = await getScanGuardAllowlist()
    allowEntries.value = data.entries
    allowInvalid.value = data.invalid
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

async function onAddAllow(entry?: string) {
  const value = (entry ?? newAllowEntry.value).trim()
  if (!value || allowBusy.value) return
  allowBusy.value = true
  try {
    const { data } = await addScanGuardAllowlistEntry(value)
    allowEntries.value = data.entries
    allowInvalid.value = data.invalid
    newAllowEntry.value = ''
    ui.pushToast(t('admin_ip_blocks.allowlist_added_toast', { entry: value }), 'success')
    await Promise.all([load(), loadWatchlist()])
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    allowBusy.value = false
  }
}

async function onRemoveAllow(entry: string) {
  const ok = await ui.confirm({
    message: t('admin_ip_blocks.confirm_allowlist_remove', { entry }),
    danger: true,
  })
  if (!ok) return
  allowBusy.value = true
  try {
    const { data } = await removeScanGuardAllowlistEntry(entry)
    allowEntries.value = data.entries
    allowInvalid.value = data.invalid
    ui.pushToast(t('admin_ip_blocks.allowlist_removed_toast', { entry }), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    allowBusy.value = false
  }
}

// --- watchlist -------------------------------------------------------------
const watchRows = ref<WatchRow[]>([])
const watchAvailable = ref(true)
const watchEnabled = ref(true)
const watchThreshold = ref(0)
const watchAuthThreshold = ref(0)

async function loadWatchlist() {
  try {
    const { data } = await getScanGuardWatchlist()
    watchRows.value = data.items
    watchAvailable.value = data.available
    watchEnabled.value = data.enabled
    watchThreshold.value = data.threshold
    watchAuthThreshold.value = data.auth_threshold
  } catch {
    // The watchlist is advisory; a failure here must not take the page down.
    // The block table below is the load-bearing half and still renders.
    watchAvailable.value = false
    watchRows.value = []
  }
}

function thresholdFor(row: WatchRow): number {
  return row.last_signal === 'auth_failure' ? watchAuthThreshold.value : watchThreshold.value
}

// --- manual block ----------------------------------------------------------
const DURATIONS = [60, 360, 1440, 10080, 43200] as const
const newSubject = ref('')
const newMinutes = ref<number | 'custom'>(60)
const customMinutes = ref(60)
const newNote = ref('')
const blocking = ref(false)

const effectiveMinutes = computed(() =>
  newMinutes.value === 'custom' ? customMinutes.value : newMinutes.value,
)
// The custom input clears to '' and accepts 0. Without this the admin gets the
// danger confirm, says yes, and then sees a 422 - a refusal that arrives after
// the decision rather than instead of it.
const canBlock = computed(
  () =>
    !blocking.value &&
    newSubject.value.trim().length > 0 &&
    Number.isInteger(effectiveMinutes.value) &&
    (effectiveMinutes.value as number) >= 1 &&
    (effectiveMinutes.value as number) <= 43200,
)

/** How many addresses a CIDR covers, as text for the confirm dialog. Display
 *  only - the server is the authority on whether the subject is acceptable.
 *
 *  Rendered as a power of two past the point where a decimal stops meaning
 *  anything: a /64 is 2^64, and calling that "10^38" (as a single Infinity
 *  bucket did) overstates it by nineteen orders of magnitude in the one dialog
 *  whose entire job is to convey scale. */
function addressCountLabel(subject: string): string | null {
  const [, bits] = subject.split('/')
  if (bits === undefined) return null
  const prefix = Number(bits)
  if (!Number.isInteger(prefix)) return null
  const width = subject.includes(':') ? 128 : 32
  if (prefix < 0 || prefix > width) return null
  const exp = width - prefix
  if (exp === 0) return null
  return exp > 40 ? `2^${exp}` : (2 ** exp).toLocaleString()
}

function durationLabel(minutes: number): string {
  const known = (DURATIONS as readonly number[]).includes(minutes)
  return known
    ? t(`admin_ip_blocks.duration.${minutes}`)
    : t('admin_ip_blocks.minutes_custom_label') + ` (${minutes})`
}

async function onBlock() {
  const subject = newSubject.value.trim()
  if (!canBlock.value) return
  const minutes = effectiveMinutes.value
  const count = addressCountLabel(subject)
  const message =
    count !== null
      ? t('admin_ip_blocks.confirm_block_network', {
          subject,
          count,
          duration: durationLabel(minutes),
        })
      : t('admin_ip_blocks.confirm_block', {
          subject,
          duration: durationLabel(minutes),
        })
  if (!(await ui.confirm({ message, danger: true }))) return

  blocking.value = true
  try {
    await createIpBlock({ subject, minutes, note: newNote.value.trim() || null })
    ui.pushToast(t('admin_ip_blocks.blocked_toast', { subject }), 'success')
    newSubject.value = ''
    newNote.value = ''
    await Promise.all([load(), loadWatchlist()])
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    blocking.value = false
  }
}

function blockFromWatchlist(ip: string) {
  newSubject.value = ip
  document.getElementById('fh-block-subject')?.focus()
}

// --- row actions -----------------------------------------------------------
const busyId = ref<number | null>(null)

async function onRelease(row: IpBlockRow) {
  const ok = await ui.confirm({
    message: t('admin_ip_blocks.confirm_release', { subject: row.subject }),
  })
  if (!ok) return
  busyId.value = row.id
  try {
    await releaseIpBlock(row.id)
    ui.pushToast(t('admin_ip_blocks.released_toast', { subject: row.subject }), 'success')
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    busyId.value = null
  }
}

async function onReleaseAndAllow(row: IpBlockRow) {
  const ok = await ui.confirm({
    message: t('admin_ip_blocks.confirm_unblock_allow', { subject: row.subject }),
  })
  if (!ok) return
  busyId.value = row.id
  try {
    const { data } = await allowIpBlock(row.id)
    allowEntries.value = data.allowlist
    ui.pushToast(t('admin_ip_blocks.allowed_toast', { subject: row.subject }), 'success')
    await Promise.all([load(), loadAllowlist(), loadWatchlist()])
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    busyId.value = null
  }
}

async function onReleaseAll() {
  const ok = await ui.confirm({
    message: t('admin_ip_blocks.confirm_release_all'),
    danger: true,
  })
  if (!ok) return
  try {
    const { data } = await releaseAllIpBlocks()
    ui.pushToast(
      t('admin_ip_blocks.released_all_toast', { count: data.released }),
      'success',
    )
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

function isLive(row: IpBlockRow): boolean {
  // `parseServerDate`, not `new Date()`. The API sends naive UTC, which
  // `new Date()` reads as browser-local: east of UTC a block still refusing
  // traffic would show "Expired" and hide its own Release button for the last
  // hours of its life, and west of UTC dead blocks would offer a Release that
  // does nothing.
  return (
    row.released_at === null &&
    parseServerDate(row.expires_at).getTime() > Date.now()
  )
}

const hasLiveBlocks = computed(() => items.value.some(isLive))

onMounted(() => {
  void load()
  void loadAllowlist()
  void loadWatchlist()
})
</script>

<template>
  <div class="fh-page" data-density="operator">
    <h1 class="fh-eyebrow">{{ t('admin_ip_blocks.title') }}</h1>
    <p class="fh-field-help intro">{{ t('admin_ip_blocks.intro') }}</p>
    <hr class="fh-rule" />

    <!-- Manual block -->
    <section class="card">
      <h2 class="sec-h2">{{ t('admin_ip_blocks.block_section') }}</h2>
      <p class="fh-field-help">{{ t('admin_ip_blocks.block_help') }}</p>
      <form class="block-form" @submit.prevent="onBlock">
        <label class="field">
          <span>{{ t('admin_ip_blocks.subject_label') }}</span>
          <input
            id="fh-block-subject"
            v-model="newSubject"
            type="text"
            class="fh-input fh-mono"
            :placeholder="t('admin_ip_blocks.subject_placeholder')"
          />
        </label>
        <label class="field">
          <span>{{ t('admin_ip_blocks.minutes_label') }}</span>
          <select v-model="newMinutes" class="fh-input">
            <option v-for="m in DURATIONS" :key="m" :value="m">
              {{ t(`admin_ip_blocks.duration.${m}`) }}
            </option>
            <option value="custom">{{ t('admin_ip_blocks.duration.custom') }}</option>
          </select>
        </label>
        <label v-if="newMinutes === 'custom'" class="field">
          <span>{{ t('admin_ip_blocks.minutes_custom_label') }}</span>
          <input
            v-model.number="customMinutes"
            type="number"
            class="fh-input"
            min="1"
            max="43200"
          />
        </label>
        <label class="field">
          <span>{{ t('admin_ip_blocks.note_label') }}</span>
          <input
            v-model="newNote"
            type="text"
            class="fh-input"
            maxlength="255"
            :placeholder="t('admin_ip_blocks.note_placeholder')"
          />
        </label>
        <button type="submit" class="fh-btn" :disabled="!canBlock">
          {{ t('admin_ip_blocks.block_cta') }}
        </button>
      </form>
    </section>

    <!-- Watchlist -->
    <section class="card">
      <h2 class="sec-h2">{{ t('admin_ip_blocks.watch_section') }}</h2>
      <p class="fh-field-help">{{ t('admin_ip_blocks.watch_help') }}</p>
      <p v-if="!watchEnabled" class="fh-field-help">
        {{ t('admin_ip_blocks.watch_disabled') }}
      </p>
      <p v-else-if="!watchAvailable" class="fh-notice" data-tone="warning">
        {{ t('admin_ip_blocks.watch_unavailable') }}
      </p>
      <p v-else-if="!watchRows.length" class="fh-field-help">
        {{ t('admin_ip_blocks.watch_empty') }}
      </p>
      <table v-else class="fh-table">
        <thead>
          <tr>
            <th>{{ t('admin_ip_blocks.watch_col_ip') }}</th>
            <th>{{ t('admin_ip_blocks.watch_col_offences') }}</th>
            <th>{{ t('admin_ip_blocks.watch_col_signal') }}</th>
            <th>{{ t('admin_ip_blocks.watch_col_path') }}</th>
            <th>{{ t('admin_ip_blocks.watch_col_seen') }}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in watchRows" :key="row.ip">
            <td class="fh-mono">{{ row.ip }}</td>
            <td>
              {{ t('admin_ip_blocks.watch_of_threshold', {
                count: row.offences, threshold: thresholdFor(row),
              }) }}
            </td>
            <td>
              {{ row.last_signal ? t(`admin_ip_blocks.reason.${row.last_signal}`) : '—' }}
            </td>
            <td class="fh-mono path" :title="row.last_path || ''">
              {{ row.last_path || '—' }}
            </td>
            <td>{{ row.last_seen ? formatDate(row.last_seen) : '—' }}</td>
            <td class="row-actions">
              <button type="button" class="fh-btn-text" @click="blockFromWatchlist(row.ip)">
                {{ t('admin_ip_blocks.watch_block_cta') }}
              </button>
              <button type="button" class="fh-btn-text" @click="onAddAllow(row.ip)">
                {{ t('admin_ip_blocks.allow_cta') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- Blocks -->
    <section class="card">
      <div class="sec-head">
        <h2 class="sec-h2">{{ t('admin_ip_blocks.list_section') }}</h2>
        <button
          v-if="hasLiveBlocks"
          type="button"
          class="fh-btn-text danger"
          @click="onReleaseAll"
        >
          {{ t('admin_ip_blocks.release_all_cta') }}
        </button>
      </div>

      <div class="filters">
        <label class="field">
          <span>{{ t('admin_ip_blocks.filter_status') }}</span>
          <select v-model="status" class="fh-input">
            <option v-for="s in STATUSES" :key="s" :value="s">
              {{ t(`admin_ip_blocks.status.${s}`) }}
            </option>
          </select>
        </label>
        <label class="field">
          <span>{{ t('admin_ip_blocks.filter_reason') }}</span>
          <select v-model="reason" class="fh-input">
            <option value="">{{ t('admin_ip_blocks.filter_any') }}</option>
            <option v-for="r in REASONS" :key="r" :value="r">
              {{ t(`admin_ip_blocks.reason.${r}`) }}
            </option>
          </select>
        </label>
        <label class="field">
          <span>{{ t('admin_ip_blocks.filter_source') }}</span>
          <select v-model="source" class="fh-input">
            <option value="">{{ t('admin_ip_blocks.filter_any') }}</option>
            <option value="auto">{{ t('admin_ip_blocks.source.auto') }}</option>
            <option value="manual">{{ t('admin_ip_blocks.source.manual') }}</option>
          </select>
        </label>
        <label class="field">
          <span>{{ t('admin_ip_blocks.search_placeholder') }}</span>
          <input v-model="search" type="search" class="fh-input fh-mono" />
        </label>
        <label class="toggle">
          <input v-model="networkOnly" type="checkbox" />
          <span>{{ t('admin_ip_blocks.filter_network_only') }}</span>
        </label>
      </div>

      <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
      <div v-else-if="errorMsg" class="fh-notice" role="alert" data-tone="error">
        {{ errorMsg }}
      </div>
      <div v-else-if="!items.length" class="empty">
        <p class="fh-field-help">
          {{
            status === 'active'
              ? t('admin_ip_blocks.no_blocks_in_force')
              : t('admin_ip_blocks.no_blocks')
          }}
          <template v-if="historyTotal > 0">
            {{ t('admin_ip_blocks.hidden_by_filter', { count: historyTotal }) }}
          </template>
        </p>
        <button
          v-if="historyTotal > 0"
          type="button"
          class="fh-btn-text"
          @click="status = 'all'"
        >
          {{ t('admin_ip_blocks.show_all_cta') }}
        </button>
      </div>
      <template v-else>
        <table class="fh-table">
          <thead>
            <tr>
              <th>{{ t('admin_ip_blocks.col_subject') }}</th>
              <th>{{ t('admin_ip_blocks.col_reason') }}</th>
              <th>{{ t('admin_ip_blocks.col_source') }}</th>
              <th>{{ t('admin_ip_blocks.col_hits') }}</th>
              <th>{{ t('admin_ip_blocks.col_path') }}</th>
              <th>{{ t('admin_ip_blocks.col_created') }}</th>
              <th>{{ t('admin_ip_blocks.col_expires') }}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in items" :key="row.id">
              <td class="fh-mono">
                {{ row.subject }}
                <span v-if="row.is_network" class="tag">
                  {{ t('admin_ip_blocks.tag_network') }}
                </span>
              </td>
              <td>
                {{ t(`admin_ip_blocks.reason.${row.reason}`) }}
                <span v-if="row.strikes > 1">×{{ row.strikes }}</span>
              </td>
              <td>{{ t(`admin_ip_blocks.source.${row.source}`) }}</td>
              <td>{{ row.hit_count }}</td>
              <td class="fh-mono path" :title="row.last_path || ''">
                {{ row.last_path || '—' }}
              </td>
              <td>{{ formatDate(row.created_at) }}</td>
              <td>
                <span v-if="row.released_at" class="fh-pill" data-state="active">
                  {{ t('admin_ip_blocks.released') }}
                </span>
                <span v-else-if="!isLive(row)" class="fh-pill">
                  {{ t('admin_ip_blocks.expired') }}
                </span>
                <template v-else>{{ formatDate(row.expires_at) }}</template>
              </td>
              <td class="row-actions">
                <template v-if="isLive(row)">
                  <button
                    type="button"
                    class="fh-btn-text"
                    :disabled="busyId === row.id"
                    @click="onRelease(row)"
                  >
                    {{ t('admin_ip_blocks.release_cta') }}
                  </button>
                  <button
                    type="button"
                    class="fh-btn-text"
                    :disabled="busyId === row.id"
                    @click="onReleaseAndAllow(row)"
                  >
                    {{ t('admin_ip_blocks.unblock_allow_cta') }}
                  </button>
                </template>
                <button
                  v-else
                  type="button"
                  class="fh-btn-text"
                  :disabled="allowBusy"
                  @click="onAddAllow(row.subject)"
                >
                  {{ t('admin_ip_blocks.allow_cta') }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="fh-field-help">{{ t('admin_ip_blocks.release_help') }}</p>
        <Pager v-model:page="page" :total="total" :page-size="pageSize" />
      </template>
    </section>

    <!-- Allowlist -->
    <section class="card">
      <h2 class="sec-h2">{{ t('admin_ip_blocks.allowlist_section') }}</h2>
      <p class="fh-field-help">{{ t('admin_ip_blocks.allowlist_help') }}</p>

      <form class="allow-form" @submit.prevent="onAddAllow()">
        <input
          v-model="newAllowEntry"
          type="text"
          class="fh-input fh-mono"
          :placeholder="t('admin_ip_blocks.allowlist_add_placeholder')"
          :aria-label="t('admin_ip_blocks.allowlist_section')"
        />
        <button type="submit" class="fh-btn" :disabled="allowBusy || !newAllowEntry.trim()">
          {{ t('admin_ip_blocks.allowlist_add_cta') }}
        </button>
      </form>

      <p v-if="!allowEntries.length" class="fh-field-help">
        {{ t('admin_ip_blocks.allowlist_empty') }}
      </p>
      <ul v-else class="allow-list">
        <li v-for="entry in allowEntries" :key="entry">
          <span class="fh-mono">{{ entry }}</span>
          <button
            type="button"
            class="fh-btn-text"
            :disabled="allowBusy"
            @click="onRemoveAllow(entry)"
          >
            {{ t('admin_ip_blocks.allowlist_remove_cta') }}
          </button>
        </li>
      </ul>

      <template v-if="allowInvalid.length">
        <h3 class="sec-h3">{{ t('admin_ip_blocks.allowlist_invalid_title') }}</h3>
        <p class="fh-notice" data-tone="warning">
          {{ t('admin_ip_blocks.allowlist_invalid_help') }}
        </p>
        <ul class="allow-list">
          <li v-for="entry in allowInvalid" :key="entry">
            <span class="fh-mono">{{ entry }}</span>
            <button
              type="button"
              class="fh-btn-text"
              :disabled="allowBusy"
              @click="onRemoveAllow(entry)"
            >
              {{ t('admin_ip_blocks.allowlist_remove_cta') }}
            </button>
          </li>
        </ul>
      </template>
    </section>
  </div>
</template>

<style scoped>
.intro {
  margin-bottom: var(--fh-space-4);
}
.card {
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-4);
  margin-bottom: var(--fh-space-5);
}
.sec-h2 {
  margin-top: 0;
}
.sec-h3 {
  margin-bottom: var(--fh-space-2);
}
.sec-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fh-space-3);
}
.block-form,
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-3);
  align-items: flex-end;
  margin: var(--fh-space-3) 0;
}
.field {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
}
.field span {
  font-size: 0.85em;
  color: var(--fh-subtle);
}
.toggle {
  display: flex;
  gap: var(--fh-space-2);
  align-items: center;
}
.allow-form {
  display: flex;
  gap: var(--fh-space-3);
  margin: var(--fh-space-3) 0;
}
.allow-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.allow-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fh-space-3);
  padding: var(--fh-space-2) 0;
  border-bottom: 1px solid var(--fh-rule);
  max-width: 46ch;
}
.row-actions {
  display: flex;
  gap: var(--fh-space-2);
  white-space: nowrap;
}
.danger {
  color: var(--fh-danger, #b42318);
}
.path {
  max-width: 28ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tag {
  font-size: 0.75em;
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
  padding: 0 0.4em;
  margin-left: 0.4em;
}
.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}
.empty {
  display: flex;
  align-items: baseline;
  gap: var(--fh-space-2);
  flex-wrap: wrap;
}
</style>
