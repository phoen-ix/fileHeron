<template>
  <section class="prefs">
    <h3 class="prefs-h3">{{ t('notif_prefs.title') }}</h3>
    <p class="fh-field-help intro">{{ t('notif_prefs.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <NotificationPreferencesTable
      v-else
      :items="items"
      :saving="saving"
      @change="onChange"
    />

    <p v-if="savedAt" class="fh-field-help saved">{{ t('common.saved') }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import NotificationPreferencesTable from '@/components/NotificationPreferencesTable.vue'
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
    // Reflect the saved value locally (the table is now controlled via props).
    const row = items.value.find((i) => i.category === cat)
    if (row) row.channel = channel
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

.saved {
  color: var(--fh-success);
}
</style>
