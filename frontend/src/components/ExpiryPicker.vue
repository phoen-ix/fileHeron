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
        :disabled="disabled"
        @click="applyPreset(preset)"
      >
        {{ t(`expiry.presets.${preset.id}`) }}
      </button>
    </div>
    <ElDatePicker
      v-model="dt"
      type="datetime"
      :placeholder="t('expiry.custom_placeholder')"
      :disabled-date="disabledDate"
      :disabled="disabled || activePreset === 'never'"
      class="custom-picker"
      format="YYYY-MM-DD HH:mm"
      value-format="YYYY-MM-DDTHH:mm:ss"
      :clearable="false"
    />
    <div class="hint">
      <span class="fh-mono">{{ hintText }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ElDatePicker } from 'element-plus'
import 'element-plus/theme-chalk/el-date-picker.css'
import 'element-plus/theme-chalk/el-input.css'
import 'element-plus/theme-chalk/el-popper.css'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { siteLocalIsoToEpochMs, siteNowPlusIso } from '@/utils/datetime'

const props = defineProps<{
  /** string = local ISO datetime; null = "Never" picked; undefined =
   *  parent hasn't initialized (picker fills with default 7d on mount). */
  modelValue: string | null | undefined
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
}>()

const { t, locale } = useI18n()

type PresetId = '1h' | '1d' | '7d' | '14d' | '30d' | 'never'
interface Preset {
  id: PresetId
  ms: number | null  // null = "never"; the sentinel emits null to the parent
}
const presets: Preset[] = [
  { id: '1h', ms: 60 * 60 * 1000 },
  { id: '1d', ms: 24 * 60 * 60 * 1000 },
  { id: '7d', ms: 7 * 24 * 60 * 60 * 1000 },
  { id: '14d', ms: 14 * 24 * 60 * 60 * 1000 },
  { id: '30d', ms: 30 * 24 * 60 * 60 * 1000 },
  { id: 'never', ms: null },
]

// dt = null means "never expires" (v1.1.4). Anything else is a
// "YYYY-MM-DDTHH:mm:ss" wall-clock string in the admin-set SITE timezone —
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
const activePreset = ref<PresetId | null>(
  props.modelValue === null ? 'never' : '7d',
)

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

function disabledDate(d: Date): boolean {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return d.getTime() < today.getTime()
}

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
  const minutes = Math.round(ms / 60_000)
  if (minutes < 60) return t('expiry.in_minutes', { n: minutes })
  const hours = Math.round(minutes / 60)
  if (hours < 24) return t('expiry.in_hours', { n: hours })
  const days = Math.round(hours / 24)
  return t('expiry.in_days', { n: days })
})

// Re-evaluate the locale-side label when language flips.
watch(locale, () => {
  /* triggers re-render of hintText via t() */
})

// Manual edits on the picker invalidate the active preset highlight.
// Skipped when dt is null (Never state — no comparison possible).
watch(dt, (newV) => {
  if (newV === null) return
  if (!activePreset.value || activePreset.value === 'never') return
  const preset = presets.find((p) => p.id === activePreset.value)
  if (!preset || preset.ms === null) return
  const expected = siteNowPlusIso(preset.ms)
  // Both are site-tz wall-clock strings; compare as instants in the site tz.
  // Allow a 60-second tolerance band — the time elapsed during click.
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
