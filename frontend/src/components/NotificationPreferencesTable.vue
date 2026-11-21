<template>
  <div class="fh-table-scroll">
    <table class="prefs-table">
      <thead>
        <tr>
          <th>{{ t('notif_prefs.col.category') }}</th>
          <th>{{ t('notif_prefs.col.channel') }}</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="(item, i) in items" :key="preferenceKey(item)">
          <!-- A category chosen per topic (operational alerts, by reason) is
               listed one row per topic; a heading opens each of its groups. -->
          <tr v-if="item.group && item.group !== items[i - 1]?.group" class="group-row">
            <th colspan="2" scope="colgroup">
              {{ t('notif_prefs.group_heading') }} · {{ groupLabel(item.group) }}
            </th>
          </tr>
          <tr :data-highlight="(!item.topic && item.category === highlight) || undefined">
            <td class="cat-cell" :class="{ 'topic-cell': item.topic }">
              {{ rowLabel(item) }}
              <span v-if="item.locked" class="locked-note">{{ t('notif_prefs.locked') }}</span>
            </td>
            <td>
              <!-- A bare <select> in a table cell has no accessible name: the
                   column header is not associated with it, so it announced only
                   its current value (audit 2026-07-30, fe-i18n-a11y-15). -->
              <select
                :value="item.channel"
                class="channel-select"
                :aria-label="t('notif_prefs.channel_for', { category: rowLabel(item) })"
                :disabled="saving || item.locked"
                @change="
                  onChange(item, ($event.target as HTMLSelectElement).value as NotificationChannel)
                "
              >
                <option v-for="c in channels" :key="c" :value="c">
                  {{ t(`notif_prefs.channel.${c}`) }}
                </option>
              </select>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
  import { useI18n } from 'vue-i18n'

  import type { NotificationCategory, NotificationChannel, PreferenceItem } from '@/types/api'
  import { preferenceKey } from '@/utils/notificationPrefs'

  defineProps<{
    items: PreferenceItem[]
    saving?: boolean
    highlight?: string | null
  }>()

  const emit = defineEmits<{
    /** `key` is what the row is saved under (utils/notificationPrefs). */
    change: [key: string, channel: NotificationChannel]
  }>()

  const { t, te } = useI18n()

  const channels: NotificationChannel[] = ['off', 'email', 'in_app', 'both']

  function catLabel(category: NotificationCategory): string {
    const key = `notif_bell.cat.${category}`
    return te(key) ? t(key) : category
  }

  /** A topic row is named for its topic; one the SPA has no words for yet (a
   *  server one release ahead) shows its id rather than nothing. */
  function rowLabel(item: PreferenceItem): string {
    if (!item.topic) return catLabel(item.category)
    const key = `notif_prefs.topic.${item.category}.${item.topic}`
    return te(key) ? t(key) : item.topic
  }

  function groupLabel(group: string): string {
    const key = `notif_prefs.group.${group}`
    return te(key) ? t(key) : group
  }

  function onChange(item: PreferenceItem, channel: NotificationChannel) {
    emit('change', preferenceKey(item), channel)
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

  .prefs-table td.topic-cell {
    padding-left: var(--fh-space-3);
  }

  .prefs-table .group-row th {
    padding-top: var(--fh-space-4);
    border-bottom: var(--fh-border);
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
