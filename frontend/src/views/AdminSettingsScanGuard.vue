<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  getScanGuardSettings,
  listIpBlocks,
  releaseIpBlock,
  updateScanGuardSettings,
  type IpBlockRow,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const { describe } = useApiError()
const { formatDate } = useSiteDateFormat()
const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)

const enabled = ref(false)
const signalProbePath = ref(true)
const signalApi404 = ref(false)
const signalAuthFailure = ref(false)
const escalation = ref(true)
const networkEscalation = ref(false)
const notifyMode = ref<'off' | 'digest' | 'every_block'>('digest')
const allowlist = ref('')
const extraPaths = ref('')
const ignorePaths = ref('')
const threshold = ref(3)
const windowSec = ref(3600)
const blockMinutes = ref(60)
const maxBlockMinutes = ref(1440)
const minDistinctPaths = ref(15)
const networkThreshold = ref(3)
const networkLookbackHours = ref(168)
const maxNewBlocksPerMin = ref(60)
const activeIpBlocks = ref(0)
const activeNetworkBlocks = ref(0)

const blocks = ref<IpBlockRow[]>([])
const includeExpired = ref(false)
const releasingId = ref<number | null>(null)

const noSignals = computed(
  () => !signalProbePath.value && !signalApi404.value && !signalAuthFailure.value,
)
const validationError = computed<string | null>(() => {
  // Mirrors the server's SCAN_GUARD_NO_SIGNALS refusal, so the admin is told
  // before they submit rather than after.
  if (enabled.value && noSignals.value) return t('admin_scan_guard.err_no_signals')
  if (maxBlockMinutes.value < blockMinutes.value)
    return t('admin_scan_guard.err_max_below_base')
  return null
})
const canSave = computed(() => !saving.value && validationError.value === null)

function apply(d: Awaited<ReturnType<typeof getScanGuardSettings>>['data']) {
  enabled.value = d.enabled
  signalProbePath.value = d.signal_probe_path
  signalApi404.value = d.signal_api_404
  signalAuthFailure.value = d.signal_auth_failure
  escalation.value = d.escalation
  networkEscalation.value = d.network_escalation
  notifyMode.value = d.notify_mode
  allowlist.value = d.allowlist
  extraPaths.value = d.extra_paths
  ignorePaths.value = d.ignore_paths
  threshold.value = d.threshold
  windowSec.value = d.window_sec
  blockMinutes.value = d.block_minutes
  maxBlockMinutes.value = d.max_block_minutes
  minDistinctPaths.value = d.min_distinct_paths
  networkThreshold.value = d.network_threshold
  networkLookbackHours.value = d.network_lookback_hours
  maxNewBlocksPerMin.value = d.max_new_blocks_per_min
  activeIpBlocks.value = d.active_ip_blocks
  activeNetworkBlocks.value = d.active_network_blocks
}

async function loadBlocks() {
  try {
    const { data } = await listIpBlocks({ active: !includeExpired.value })
    blocks.value = data.items
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getScanGuardSettings()
    apply(data)
    await loadBlocks()
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onSave() {
  if (!canSave.value) return
  saving.value = true
  errorMsg.value = null
  try {
    const { data } = await updateScanGuardSettings({
      enabled: enabled.value,
      signal_probe_path: signalProbePath.value,
      signal_api_404: signalApi404.value,
      signal_auth_failure: signalAuthFailure.value,
      escalation: escalation.value,
      network_escalation: networkEscalation.value,
      notify_mode: notifyMode.value,
      allowlist: allowlist.value,
      extra_paths: extraPaths.value,
      ignore_paths: ignorePaths.value,
      threshold: threshold.value,
      window_sec: windowSec.value,
      block_minutes: blockMinutes.value,
      max_block_minutes: maxBlockMinutes.value,
      min_distinct_paths: minDistinctPaths.value,
      network_threshold: networkThreshold.value,
      network_lookback_hours: networkLookbackHours.value,
      max_new_blocks_per_min: maxNewBlocksPerMin.value,
    })
    apply(data)
    ui.pushToast(t('admin_scan_guard.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

async function onRelease(row: IpBlockRow) {
  releasingId.value = row.id
  try {
    await releaseIpBlock(row.id)
    ui.pushToast(t('admin_scan_guard.released_toast', { subject: row.subject }), 'success')
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    releasingId.value = null
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="policy-page" data-density="operator">
    <h1 class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_scan_guard.title') }}
    </h1>
    <p class="fh-field-help intro">{{ t('admin_scan_guard.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <form v-else class="policy-form" @submit.prevent="onSave">
      <fieldset class="toggle-fieldset">
        <legend class="legend">{{ t('admin_scan_guard.master_section') }}</legend>
        <label class="toggle">
          <input v-model="enabled" type="checkbox" />
          <span><strong>{{ t('admin_scan_guard.enabled_label') }}</strong></span>
        </label>
        <p class="fh-field-help">{{ t('admin_scan_guard.enabled_help') }}</p>
        <p v-if="enabled" class="fh-field-help">
          {{ t('admin_scan_guard.live_counts', {
            ips: activeIpBlocks, nets: activeNetworkBlocks,
          }) }}
        </p>
      </fieldset>

      <fieldset class="toggle-fieldset">
        <legend class="legend">{{ t('admin_scan_guard.signals_section') }}</legend>
        <label class="toggle">
          <input v-model="signalProbePath" type="checkbox" />
          <span><strong>{{ t('admin_scan_guard.probe_path_label') }}</strong></span>
        </label>
        <p class="fh-field-help">{{ t('admin_scan_guard.probe_path_help') }}</p>

        <label class="toggle">
          <input v-model="signalApi404" type="checkbox" />
          <span><strong>{{ t('admin_scan_guard.api_404_label') }}</strong></span>
        </label>
        <!-- The false-positive warning belongs next to the switch, not in a doc. -->
        <p class="fh-notice" data-tone="warning">{{ t('admin_scan_guard.api_404_help') }}</p>

        <label class="toggle">
          <input v-model="signalAuthFailure" type="checkbox" />
          <span><strong>{{ t('admin_scan_guard.auth_failure_label') }}</strong></span>
        </label>
        <p class="fh-field-help">{{ t('admin_scan_guard.auth_failure_help') }}</p>
      </fieldset>

      <fieldset class="toggle-fieldset">
        <legend class="legend">{{ t('admin_scan_guard.thresholds_section') }}</legend>
        <label class="num-field">
          <span>{{ t('admin_scan_guard.threshold_label') }}</span>
          <input v-model.number="threshold" type="number" class="fh-input" min="1" max="1000" />
        </label>
        <label class="num-field">
          <span>{{ t('admin_scan_guard.window_label') }}</span>
          <input v-model.number="windowSec" type="number" class="fh-input" min="30" max="86400" />
        </label>
        <label class="num-field">
          <span>{{ t('admin_scan_guard.block_minutes_label') }}</span>
          <input v-model.number="blockMinutes" type="number" class="fh-input" min="1" max="43200" />
        </label>
        <label class="num-field">
          <span>{{ t('admin_scan_guard.max_block_minutes_label') }}</span>
          <input v-model.number="maxBlockMinutes" type="number" class="fh-input" min="1" max="43200" />
        </label>
        <label class="toggle">
          <input v-model="escalation" type="checkbox" />
          <span><strong>{{ t('admin_scan_guard.escalation_label') }}</strong></span>
        </label>
        <p class="fh-field-help">{{ t('admin_scan_guard.escalation_help') }}</p>
        <label v-if="signalApi404" class="num-field">
          <span>{{ t('admin_scan_guard.min_distinct_label') }}</span>
          <input v-model.number="minDistinctPaths" type="number" class="fh-input" min="1" max="500" />
        </label>
      </fieldset>

      <fieldset class="toggle-fieldset">
        <legend class="legend">{{ t('admin_scan_guard.network_section') }}</legend>
        <label class="toggle">
          <input v-model="networkEscalation" type="checkbox" />
          <span><strong>{{ t('admin_scan_guard.network_label') }}</strong></span>
        </label>
        <p class="fh-notice" data-tone="warning">{{ t('admin_scan_guard.network_help') }}</p>
        <template v-if="networkEscalation">
          <label class="num-field">
            <span>{{ t('admin_scan_guard.network_threshold_label') }}</span>
            <input v-model.number="networkThreshold" type="number" class="fh-input" min="2" max="254" />
          </label>
          <label class="num-field">
            <span>{{ t('admin_scan_guard.network_lookback_label') }}</span>
            <input v-model.number="networkLookbackHours" type="number" class="fh-input" min="1" max="8760" />
          </label>
        </template>
      </fieldset>

      <fieldset class="toggle-fieldset">
        <legend class="legend">{{ t('admin_scan_guard.allowlist_section') }}</legend>
        <!-- Deliberately the most prominent free-text field on the page: it is
             the admin's own escape hatch from a control that denies service. -->
        <textarea
v-model="allowlist" class="fh-input" rows="3"
                  :aria-label="t('admin_scan_guard.allowlist_section')"
                  :placeholder="t('admin_scan_guard.allowlist_placeholder')" />
        <p class="fh-field-help">{{ t('admin_scan_guard.allowlist_help') }}</p>
        <label class="num-field">
          <span>{{ t('admin_scan_guard.extra_paths_label') }}</span>
          <input
v-model="extraPaths" type="text" class="fh-input"
                 :placeholder="t('admin_scan_guard.extra_paths_placeholder')" />
        </label>
        <label class="num-field">
          <span>{{ t('admin_scan_guard.ignore_paths_label') }}</span>
          <input v-model="ignorePaths" type="text" class="fh-input" />
        </label>
      </fieldset>

      <fieldset class="toggle-fieldset">
        <legend class="legend">{{ t('admin_scan_guard.notify_section') }}</legend>
        <label v-for="mode in (['off', 'digest', 'every_block'] as const)" :key="mode" class="toggle">
          <input v-model="notifyMode" type="radio" :value="mode" />
          <span>
            <strong>{{ t(`admin_scan_guard.notify.${mode}`) }}</strong>
            <em class="fh-field-help">{{ t(`admin_scan_guard.notify_help.${mode}`) }}</em>
          </span>
        </label>
      </fieldset>

      <div v-if="validationError" class="fh-notice" data-tone="warning">
        {{ validationError }}
      </div>
      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">
        {{ errorMsg }}
      </div>
      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="!canSave">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>

    <!-- Blocked sources -->
    <section v-if="!loading" class="card">
      <h2 class="sec-h2">{{ t('admin_scan_guard.blocks_title') }}</h2>
      <label class="toggle">
        <input v-model="includeExpired" type="checkbox" @change="loadBlocks" />
        <span>{{ t('admin_scan_guard.include_expired') }}</span>
      </label>
      <p v-if="!blocks.length" class="fh-field-help">
        {{ t('admin_scan_guard.no_blocks') }}
      </p>
      <table v-else class="fh-table">
        <thead>
          <tr>
            <th>{{ t('admin_scan_guard.col_subject') }}</th>
            <th>{{ t('admin_scan_guard.col_reason') }}</th>
            <th>{{ t('admin_scan_guard.col_hits') }}</th>
            <th>{{ t('admin_scan_guard.col_path') }}</th>
            <th>{{ t('admin_scan_guard.col_expires') }}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in blocks" :key="row.id">
            <td class="fh-mono">
              {{ row.subject }}
              <span v-if="row.is_network" class="tag">{{ t('admin_scan_guard.tag_network') }}</span>
            </td>
            <td>{{ row.reason }} <span v-if="row.strikes > 1">×{{ row.strikes }}</span></td>
            <td>{{ row.hit_count }}</td>
            <td class="fh-mono path">{{ row.last_path || '—' }}</td>
            <td>{{ row.released_at ? t('admin_scan_guard.released') : formatDate(row.expires_at) }}</td>
            <td>
              <button
                v-if="!row.released_at"
                type="button"
                class="fh-btn-text"
                :disabled="releasingId === row.id"
                @click="onRelease(row)"
              >
                {{ t('admin_scan_guard.release_cta') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.intro {
  margin-bottom: var(--fh-space-5);
}
.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}
.toggle-fieldset {
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-4);
  margin-bottom: var(--fh-space-4);
}
.legend {
  font-weight: 600;
  padding: 0 var(--fh-space-2);
}
.toggle {
  display: flex;
  gap: var(--fh-space-2);
  align-items: flex-start;
  margin: var(--fh-space-2) 0;
}
.num-field {
  display: flex;
  gap: var(--fh-space-3);
  align-items: center;
  margin: var(--fh-space-2) 0;
}
.num-field span {
  min-width: 22ch;
}
.card {
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-4);
  margin-top: var(--fh-space-5);
}
.sec-h2 {
  margin-top: 0;
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
.actions {
  margin-top: var(--fh-space-4);
}
</style>
