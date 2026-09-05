<template>
  <div class="expiry-picker">
    <label class="fh-field-label">{{ t('expiry.label') }}</label>
    <div class="presets">
      <button
        v-for="preset in presets"
        :key="preset.id"
        type="button"
        class="preset-btn"
        :class="{ active: activePreset === preset.id }"
        :aria-pressed="activePreset === preset.id"
        :disabled="disabled"
        @click="applyPreset(preset)"
      >
        {{ t(`expiry.presets.${preset.id}`) }}
      </button>
    </div>
    <input
      v-model="inputValue"
      :aria-label="t('expiry.custom_placeholder')"
      type="datetime-local"
      class="fh-field-input custom-picker"
      :placeholder="t('expiry.custom_placeholder')"
      :min="minAttr"
      :disabled="disabled || activePreset === 'never'"
    />
    <div class="hint">
      <span class="fh-mono">{{ hintText }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { siteLocalIsoToEpochMs, siteNowPlusIso } from '@/utils/datetime'

const props = defineProps<{
  /** string = local ISO datetime; null = "Never" picked; undefined =
   *  parent hasn't initialized (picker fills with default 7d on mount). */
  modelValue: string | null | undefined
  disabled?: boolean
  /** Optional override of the preset buttons (default = the share set).
   *  e.g. API tokens pass ['7d','30d','90d','1y','never']. */
  presets?: readonly PresetId[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
}>()

const { t, locale } = useI18n()

const DAY = 24 * 60 * 60 * 1000
type PresetId = '1h' | '1d' | '7d' | '14d' | '30d' | '60d' | '90d' | '1y' | 'never'
const PRESET_MS: Record<PresetId, number | null> = {
  '1h': 60 * 60 * 1000,
  '1d': DAY,
  '7d': 7 * DAY,
  '14d': 14 * DAY,
  '30d': 30 * DAY,
  '60d': 60 * DAY,
  '90d': 90 * DAY,
  '1y': 365 * DAY,
  never: null,
}
interface Preset {
  id: PresetId
  ms: number | null  // null = "never"; the sentinel emits null to the parent
}
const DEFAULT_PRESET_IDS: PresetId[] = ['1h', '1d', '7d', '14d', '30d', 'never']
const presets: Preset[] = (props.presets ?? DEFAULT_PRESET_IDS).map((id) => ({
  id,
  ms: PRESET_MS[id],
}))

// dt = null means "never expires" (v1.1.4). Anything else is a
// "YYYY-MM-DDTHH:mm:ss" wall-clock string in the admin-set SITE timezone -
// the same zone the app *displays* expiry in (formatInSiteTime). Anchoring
// the picker to the site tz (via siteNowPlusIso) instead of the browser tz
// means a "7 days" pick lands exactly 7 days out and shows the time the user
// picked, even when the viewer's browser tz differs from the site tz.
// ShareCreate converts this string back to UTC with siteLocalIsoToUtcIso.
const dt = ref<string | null>(
  props.modelValue === undefined
    ? siteNowPlusIso(7 * 24 * 60 * 60 * 1000)
    : props.modelValue,
)
/** Which preset button the initial value corresponds to, if any. This was a
 *  hard-coded '7d' for every non-null value, so a picker seeded with the token
 *  forms' 90-day default highlighted "7 days" next to a date three months out. */
function presetFor(value: string | null | undefined): PresetId | null {
  if (value === null) return 'never'
  if (value === undefined) return '7d' // the default seeded into `dt` above
  const ahead = siteLocalIsoToEpochMs(value) - Date.now()
  const hit = presets.find((p) => p.ms !== null && Math.abs(ahead - p.ms) <= 60_000)
  return hit?.id ?? null
}
const activePreset = ref<PresetId | null>(presetFor(props.modelValue))

watch(dt, (v) => {
  emit('update:modelValue', v)
})

watch(
  () => props.modelValue,
  (v) => {
    if (v === undefined) return
    if (v !== dt.value) dt.value = v
  },
)

// Emit the initial default upward so the parent has a fully-known value
// without needing to mirror our preset table.
if (props.modelValue === undefined) emit('update:modelValue', dt.value)

function applyPreset(preset: Preset) {
  if (preset.ms === null) {
    dt.value = null
    activePreset.value = 'never'
    return
  }
  dt.value = siteNowPlusIso(preset.ms)
  activePreset.value = preset.id
}

// Bridge the native <input type="datetime-local"> (minute precision,
// "YYYY-MM-DDTHH:mm") to the component's site-tz wall-clock contract
// ("YYYY-MM-DDTHH:mm:ss"). Null = "never".
const inputValue = computed<string>({
  get: () => (dt.value ? dt.value.slice(0, 16) : ''),
  set: (v: string) => {
    dt.value = v ? `${v}:00` : null
  },
})

// Prevent picking a past instant - the site-tz "now" at page load, to the
// minute. Evaluated once (no reactive deps); a coarse floor is sufficient.
const minAttr = siteNowPlusIso(0).slice(0, 16)

const expiresInMs = computed(() => {
  if (!dt.value) return 0
  // dt is a site-tz wall-clock string; resolve it to an instant in the site
  // tz before differencing against now.
  return siteLocalIsoToEpochMs(dt.value) - Date.now()
})

const hintText = computed(() => {
  if (activePreset.value === 'never' || dt.value === null) {
    return t('expiry.never_help')
  }
  const ms = expiresInMs.value
  if (ms <= 0) return t('expiry.in_past')
  // The third argument is the plural COUNT. vue-i18n selects the form from it,
  // not from the interpolation payload - so `t(key, { n })` alone always
  // rendered the plural branch and every one-day expiry read "in 1 days" /
  // "in 1 Tagen" (audit 2026-07-30, fe-i18n-a11y-12).
  const minutes = Math.round(ms / 60_000)
  if (minutes < 60) return t('expiry.in_minutes', { n: minutes }, minutes)
  const hours = Math.round(minutes / 60)
  if (hours < 24) return t('expiry.in_hours', { n: hours }, hours)
  const days = Math.round(hours / 24)
  return t('expiry.in_days', { n: days }, days)
})

// Re-evaluate the locale-side label when language flips.
watch(locale, () => {
  /* triggers re-render of hintText via t() */
})

// Manual edits on the picker invalidate the active preset highlight.
// Skipped when dt is null (Never state - no comparison possible).
watch(dt, (newV) => {
  if (newV === null) return
  if (!activePreset.value || activePreset.value === 'never') return
  const preset = presets.find((p) => p.id === activePreset.value)
  if (!preset || preset.ms === null) return
  const expected = siteNowPlusIso(preset.ms)
  // Both are site-tz wall-clock strings; compare as instants in the site tz.
  // Allow a 60-second tolerance band - the time elapsed during click.
  if (Math.abs(siteLocalIsoToEpochMs(newV) - siteLocalIsoToEpochMs(expected)) > 60_000) {
    activePreset.value = null
  }
})
</script>

<style scoped>
.expiry-picker {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  margin-bottom: var(--fh-space-3);
}

.presets {
  display: flex;
  gap: var(--fh-space-1);
  flex-wrap: wrap;
}

.preset-btn {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: var(--fh-space-1) var(--fh-space-3);
  background: transparent;
  color: var(--fh-subtle);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  cursor: pointer;
  transition:
    border-color var(--fh-duration-fast) var(--fh-easing),
    color var(--fh-duration-fast) var(--fh-easing),
    background var(--fh-duration-fast) var(--fh-easing);
}

.preset-btn:hover:not(:disabled) {
  border-color: var(--fh-ink-soft);
  color: var(--fh-ink);
}

.preset-btn.active {
  background: var(--fh-ink);
  border-color: var(--fh-ink);
  color: var(--fh-paper);
}

.preset-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.custom-picker {
  width: 100%;
}

.hint {
  color: var(--fh-subtle);
  font-size: var(--fh-text-body-sm);
}
</style>
