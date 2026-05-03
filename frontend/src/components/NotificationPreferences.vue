<template>
  <section class="prefs">
    <h3 class="prefs-h3">{{ t('notif_prefs.title') }}</h3>
    <p class="fh-field-help intro">{{ t('notif_prefs.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <table v-else class="prefs-table">
      <thead>
        <tr>
          <th>{{ t('notif_prefs.col.category') }}</th>
          <th>{{ t('notif_prefs.col.channel') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.category">
          <td class="cat-cell">{{ t(`notif_bell.cat.${item.category}`) }}</td>
          <td>
            <select
              v-model="item.channel"
              class="channel-select"
              :disabled="saving"
              @change="onChange(item.category, item.channel)"
            >
              <option v-for="c in channels" :key="c" :value="c">
                {{ t(`notif_prefs.channel.${c}`) }}
              </option>
            </select>
          </td>
        </tr>
      </tbody>
    </table>

    <p v-if="savedAt" class="fh-field-help saved">{{ t('common.saved') }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  getPreferences,
  updatePreferences,
} from '@/api/notifications'
import type {
  NotificationCategory,
  NotificationChannel,
  PreferenceItem,
} from '@/types/api'

const { t } = useI18n()

const items = ref<PreferenceItem[]>([])
const loading = ref(true)
const saving = ref(false)
const savedAt = ref<number | null>(null)

const channels: NotificationChannel[] = ['off', 'email', 'in_app', 'both']

async function load() {
  loading.value = true
  try {
    const { data } = await getPreferences()
    items.value = data.items
  } finally {
    loading.value = false
  }
}

async function onChange(cat: NotificationCategory, channel: NotificationChannel) {
  saving.value = true
  try {
    await updatePreferences({ [cat]: channel } as Record<NotificationCategory, NotificationChannel>)
    savedAt.value = Date.now()
    setTimeout(() => {
      if (savedAt.value && Date.now() - savedAt.value > 1500) savedAt.value = null
    }, 1700)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.prefs {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.prefs-h3 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  margin: 0 0 var(--fh-space-2);
}

.intro {
  margin: 0 0 var(--fh-space-2);
  max-width: 60ch;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-3) 0;
}

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

.cat-cell {
  color: var(--fh-ink);
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

.saved {
  color: var(--fh-success);
}
</style>
