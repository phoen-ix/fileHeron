<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  getAdvancedSettings,
  updateAdvancedSettings,
  type AdvancedSettingItem,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'

const { t, te } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const loading = ref(true)
const saving = ref(false)
const errorMsg = ref<string | null>(null)

const items = ref<AdvancedSettingItem[]>([])
// Working copy of each value, keyed by setting key.
const draft = ref<Record<string, number | boolean | string>>({})

// Stable group order; anything unknown falls to the end.
const GROUP_ORDER = ['sessions', 'rate_limits', 'retention', 'uploads', 'security', 'branding']

const groups = computed(() => {
  const by: Record<string, AdvancedSettingItem[]> = {}
  for (const it of items.value) (by[it.group] ??= []).push(it)
  return Object.keys(by)
    .sort((a, b) => {
      const ia = GROUP_ORDER.indexOf(a)
      const ib = GROUP_ORDER.indexOf(b)
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
    })
    .map((g) => ({ group: g, items: by[g] }))
})

// True if any draft differs from the loaded effective value.
const dirty = computed(() =>
  items.value.some((it) => draft.value[it.key] !== it.value),
)

function labelFor(key: string): string {
  const k = `admin_advanced.keys.${key}`
  return te(k) ? t(k) : key
}
function helpFor(key: string): string {
  const k = `admin_advanced.help.${key}`
  return te(k) ? t(k) : ''
}
function groupLabel(group: string): string {
  const k = `admin_advanced.groups.${group}`
  return te(k) ? t(k) : group
}

function syncDraft() {
  const d: Record<string, number | boolean | string> = {}
  for (const it of items.value) d[it.key] = it.value
  draft.value = d
}

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getAdvancedSettings()
    items.value = data.items
    syncDraft()
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

function resetOne(it: AdvancedSettingItem) {
  // Visually fall back to the default; the actual reset is sent as null on save.
  draft.value[it.key] = it.default
}

async function onSave() {
  saving.value = true
  errorMsg.value = null
  // Only send changed keys. A value equal to the env default is sent as
  // null (reset → delete the override).
  const updates: Record<string, number | boolean | string | null> = {}
  for (const it of items.value) {
    const v = draft.value[it.key]
    if (v === it.value) continue
    updates[it.key] = v === it.default ? null : v
  }
  if (Object.keys(updates).length === 0) {
    saving.value = false
    return
  }
  try {
    const { data } = await updateAdvancedSettings({ updates })
    items.value = data.items
    syncDraft()
    ui.pushToast(t('admin_advanced.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}
onMounted(load)
</script>

<template>
  <div class="advanced-settings">
    <span class="fh-eyebrow">{{ t('admin_advanced.eyebrow') }}</span>
    <p class="fh-field-help intro">{{ t('admin_advanced.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <form v-else @submit.prevent="onSave">
      <section v-for="g in groups" :key="g.group" class="group">
        <h2 class="group-h2">{{ groupLabel(g.group) }}</h2>
        <div v-for="it in g.items" :key="it.key" class="field-row">
          <div class="field-text">
            <span class="field-label">{{ labelFor(it.key) }}</span>
            <span v-if="helpFor(it.key)" class="fh-field-help">{{ helpFor(it.key) }}</span>
          </div>
          <div class="field-control">
            <label v-if="it.kind === 'bool'" class="switch">
              <input v-model="draft[it.key]" type="checkbox" />
            </label>
            <input
              v-else-if="it.kind === 'int'"
              v-model.number="draft[it.key]"
              class="fh-field-input num"
              type="number"
              :min="it.min ?? undefined"
              :max="it.max ?? undefined"
              :placeholder="String(it.default)"
            />
            <input
              v-else
              v-model.trim="draft[it.key]"
              class="fh-field-input"
              type="text"
              :placeholder="String(it.default)"
            />
            <button
              type="button"
              class="reset-btn"
              :disabled="draft[it.key] === it.default"
              :title="t('admin_advanced.reset_title', { default: String(it.default) })"
              @click="resetOne(it)"
            >
              {{ t('admin_advanced.reset') }}
            </button>
          </div>
        </div>
      </section>

      <div v-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="saving || !dirty">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.advanced-settings { max-width: 760px; }
.intro { margin-bottom: var(--fh-space-4); max-width: 64ch; }
.loading { color: var(--fh-subtle); padding: var(--fh-space-4) 0; }
.group { margin-bottom: var(--fh-space-5); }
.group-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.1rem;
  margin: 0 0 var(--fh-space-3);
  padding-bottom: var(--fh-space-2);
  border-bottom: var(--fh-border);
}
.field-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--fh-space-3);
  align-items: start;
  padding: var(--fh-space-3) 0;
  border-bottom: var(--fh-border);
}
.field-text { display: flex; flex-direction: column; gap: 2px; }
.field-label { color: var(--fh-ink); font-size: var(--fh-text-body-sm); }
.field-control { display: flex; align-items: center; gap: var(--fh-space-2); }
.num { width: 120px; text-align: right; }
.reset-btn {
  background: none;
  border: none;
  color: var(--fh-subtle);
  font-size: var(--fh-text-mono-sm);
  cursor: pointer;
  text-decoration: underline;
}
.reset-btn:disabled { opacity: 0.35; cursor: default; text-decoration: none; }
.actions { margin-top: var(--fh-space-4); }
</style>
