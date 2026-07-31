<template>
  <table class="prefs-table">
    <thead>
      <tr>
        <th>{{ t('notif_prefs.col.category') }}</th>
        <th>{{ t('notif_prefs.col.channel') }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="item in items" :key="item.category" :data-highlight="item.category === highlight || undefined">
        <td class="cat-cell">
          {{ catLabel(item.category) }}
          <span v-if="item.locked" class="locked-note">{{ t('notif_prefs.locked') }}</span>
        </td>
        <td>
          <!-- A bare <select> in a table cell has no accessible name: the
               column header is not associated with it, so it announced only
               its current value (audit 2026-07-30, fe-i18n-a11y-15). -->
          <select
            :value="item.channel"
            class="channel-select"
            :aria-label="t('notif_prefs.channel_for', { category: catLabel(item.category) })"
            :disabled="saving || item.locked"
            @change="onChange(item.category, ($event.target as HTMLSelectElement).value as NotificationChannel)"
          >
            <option v-for="c in channels" :key="c" :value="c">
              {{ t(`notif_prefs.channel.${c}`) }}
            </option>
          </select>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import type {
  NotificationCategory,
  NotificationChannel,
  PreferenceItem,
} from '@/types/api'

defineProps<{
  items: PreferenceItem[]
  saving?: boolean
  highlight?: string | null
}>()

const emit = defineEmits<{
  change: [category: NotificationCategory, channel: NotificationChannel]
}>()

const { t } = useI18n()

const channels: NotificationChannel[] = ['off', 'email', 'in_app', 'both']

function catLabel(category: NotificationCategory): string {
  const key = `notif_bell.cat.${category}`
  const label = t(key)
  return label === key ? category : label
}

function onChange(category: NotificationCategory, channel: NotificationChannel) {
  emit('change', category, channel)
}
</script>

<style scoped>
.prefs-table {
  width: 100%;
  border-collapse: collapse;
}

.prefs-table th {
  text-align: left;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
  font-weight: 500;
  padding: var(--fh-space-2) 0;
  border-bottom: var(--fh-border);
}

.prefs-table td {
  padding: var(--fh-space-2) 0;
  border-bottom: var(--fh-border);
}

.prefs-table tr[data-highlight] td {
  background: var(--fh-accent-soft);
}

.cat-cell {
  color: var(--fh-ink);
}

.locked-note {
  display: inline-block;
  margin-left: var(--fh-space-2);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--fh-subtle);
}

.channel-select {
  font-family: inherit;
  font-size: var(--fh-text-body-sm);
  background: transparent;
  border: var(--fh-border-strong);
  border-radius: var(--fh-radius-sm);
  padding: 4px 8px;
  color: var(--fh-ink);
}

.channel-select:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
