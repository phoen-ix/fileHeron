<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getScanGuardSettings, updateScanGuardSettings } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const { describe } = useApiError()
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
const watchlist = ref(true)
const notifyMode = ref<'off' | 'every_block'>('off')
const extraPaths = ref('')
const ignorePaths = ref('')
const threshold = ref(3)
const authThreshold = ref(15)
const windowSec = ref(3600)
const blockMinutes = ref(60)
const maxBlockMinutes = ref(1440)
const minDistinctPaths = ref(15)
const networkThreshold = ref(3)
const networkLookbackHours = ref(168)
const maxNewBlocksPerMin = ref(60)
const networkPrefixV6 = ref(64)
const activeIpBlocks = ref(0)
const activeNetworkBlocks = ref(0)

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
  watchlist.value = d.watchlist
  notifyMode.value = d.notify_mode
  extraPaths.value = d.extra_paths
  ignorePaths.value = d.ignore_paths
  threshold.value = d.threshold
  authThreshold.value = d.auth_threshold
  windowSec.value = d.window_sec
  blockMinutes.value = d.block_minutes
  maxBlockMinutes.value = d.max_block_minutes
  minDistinctPaths.value = d.min_distinct_paths
  networkThreshold.value = d.network_threshold
  networkLookbackHours.value = d.network_lookback_hours
  maxNewBlocksPerMin.value = d.max_new_blocks_per_min
  networkPrefixV6.value = d.network_prefix_v6
  activeIpBlocks.value = d.active_ip_blocks
  activeNetworkBlocks.value = d.active_network_blocks
}

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getScanGuardSettings()
    apply(data)
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
      watchlist: watchlist.value,
      notify_mode: notifyMode.value,
      extra_paths: extraPaths.value,
      ignore_paths: ignorePaths.value,
      threshold: threshold.value,
      auth_threshold: authThreshold.value,
      window_sec: windowSec.value,
      block_minutes: blockMinutes.value,
      max_block_minutes: maxBlockMinutes.value,
      min_distinct_paths: minDistinctPaths.value,
      network_threshold: networkThreshold.value,
      network_lookback_hours: networkLookbackHours.value,
      max_new_blocks_per_min: maxNewBlocksPerMin.value,
      network_prefix_v6: networkPrefixV6.value,
    })
    apply(data)
    ui.pushToast(t('admin_scan_guard.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
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
        <p class="fh-field-help">
          {{ t('admin_scan_guard.blocks_moved_help') }}
          <RouterLink :to="{ name: 'admin-ip-blocks' }">
            {{ t('admin_scan_guard.manage_blocks_cta') }}
          </RouterLink>
        </p>

        <label class="toggle">
          <input v-model="watchlist" type="checkbox" />
          <span><strong>{{ t('admin_scan_guard.watchlist_label') }}</strong></span>
        </label>
        <p class="fh-field-help">{{ t('admin_scan_guard.watchlist_help') }}</p>
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
        <label v-if="signalAuthFailure" class="num-field">
          <span>{{ t('admin_scan_guard.auth_threshold_label') }}</span>
          <input
            v-model.number="authThreshold" type="number" class="fh-input"
            min="5" max="500"
          />
        </label>
        <p v-if="signalAuthFailure" class="fh-field-help">
          {{ t('admin_scan_guard.auth_threshold_help') }}
        </p>
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
          <label class="num-field">
            <span>{{ t('admin_scan_guard.v6_prefix_label') }}</span>
            <input v-model.number="networkPrefixV6" type="number" class="fh-input" min="56" max="128" />
          </label>
          <!-- The tenancy reality, not "one site". Widening this is the single
               most collateral-prone control on the page. -->
          <p class="fh-notice" data-tone="warning">{{ t('admin_scan_guard.v6_prefix_help') }}</p>
        </template>
      </fieldset>

      <fieldset class="toggle-fieldset">
        <legend class="legend">{{ t('admin_scan_guard.paths_section') }}</legend>
        <!-- The allowlist used to be a free-text textarea here. It moved to the
             blocked-sources page: as a whole-CSV field on this form it was a
             second writer, so saving these settings erased entries added there.
             Paths stay - they are policy, not state. -->
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
        <label v-for="mode in (['off', 'every_block'] as const)" :key="mode" class="toggle">
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
