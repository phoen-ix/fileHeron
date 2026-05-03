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
      :disabled="disabled"
      class="custom-picker"
      format="YYYY-MM-DD HH:mm"
      value-format="YYYY-MM-DDTHH:mm:ss"
      :clearable="false"
    />
    <div class="hint">
      <span class="fh-mono">{{ formatExpiresIn(dt) }}</span>
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

const props = defineProps<{
  modelValue: string | null
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const { t, locale } = useI18n()

interface Preset {
  id: '1h' | '1d' | '7d' | '14d' | '30d'
  ms: number
}
const presets: Preset[] = [
  { id: '1h', ms: 60 * 60 * 1000 },
  { id: '1d', ms: 24 * 60 * 60 * 1000 },
  { id: '7d', ms: 7 * 24 * 60 * 60 * 1000 },
  { id: '14d', ms: 14 * 24 * 60 * 60 * 1000 },
  { id: '30d', ms: 30 * 24 * 60 * 60 * 1000 },
]

function isoLocal(d: Date): string {
  // Element Plus's datetime picker emits "YYYY-MM-DDTHH:mm:ss" in *local*
  // time; we keep it in that format and let the backend interpret it as
  // UTC (per the Phase 3a convention). For accuracy, downstream Date
  // construction must use new Date(s + 'Z') if it needs UTC.
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const dt = ref<string>(props.modelValue ?? isoLocal(new Date(Date.now() + 7 * 24 * 60 * 60 * 1000)))
const activePreset = ref<Preset['id'] | null>('7d')

watch(dt, (v) => {
  if (v) emit('update:modelValue', v)
})

watch(
  () => props.modelValue,
  (v) => {
    if (v && v !== dt.value) dt.value = v
  },
)

// Emit the initial default upward so the parent has a non-null value
// without needing to mirror our preset table.
if (!props.modelValue) emit('update:modelValue', dt.value)

function applyPreset(preset: Preset) {
  const future = new Date(Date.now() + preset.ms)
  dt.value = isoLocal(future)
  activePreset.value = preset.id
}

function disabledDate(d: Date): boolean {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return d.getTime() < today.getTime()
}

const expiresInMs = computed(() => {
  if (!dt.value) return 0
  return new Date(dt.value).getTime() - Date.now()
})

function formatExpiresIn(_v: string | null): string {
  const ms = expiresInMs.value
  if (ms <= 0) return t('expiry.in_past')
  const minutes = Math.round(ms / 60_000)
  if (minutes < 60) return t('expiry.in_minutes', { n: minutes })
  const hours = Math.round(minutes / 60)
  if (hours < 24) return t('expiry.in_hours', { n: hours })
  const days = Math.round(hours / 24)
  return t('expiry.in_days', { n: days })
}

// Re-evaluate the locale-side label when language flips.
watch(locale, () => {
  /* triggers re-render of formatExpiresIn via t() */
})

// Manual edits on the picker invalidate the active preset highlight.
watch(dt, (newV) => {
  if (!activePreset.value) return
  const preset = presets.find((p) => p.id === activePreset.value)
  if (!preset) return
  const expected = isoLocal(new Date(Date.now() + preset.ms))
  // Allow a 60-second tolerance band — the time elapsed during click.
  if (Math.abs(new Date(newV).getTime() - new Date(expected).getTime()) > 60_000) {
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
