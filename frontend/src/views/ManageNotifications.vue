<template>
  <div class="fh-prose manage-notifications">
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <div v-else-if="errorCode" class="error-state">
      <span class="fh-eyebrow">{{ t('manage_notifications.eyebrow') }}</span>
      <h1 class="fh-display-md">{{ t('manage_notifications.invalid_title') }}</h1>
      <p class="fh-field-help">
        {{ t(`manage_notifications.errors.${errorCode}`, t('manage_notifications.errors.generic')) }}
      </p>
    </div>

    <template v-else>
      <span class="fh-eyebrow fh-rise" data-stagger="1">{{ t('manage_notifications.eyebrow') }}</span>
      <h1 class="fh-display fh-rise" data-stagger="2">{{ t('manage_notifications.title') }}</h1>
      <p class="fh-field-help fh-rise intro" data-stagger="2">
        {{ displayName ? t('manage_notifications.intro_named', { name: displayName }) : t('manage_notifications.intro') }}
      </p>

      <div v-if="confirmedCategory" class="confirm-banner fh-rise" data-stagger="3">
        <span>{{ t('manage_notifications.unsubscribed', { category: confirmedLabel }) }}</span>
        <button type="button" class="fh-btn-text" :disabled="saving" @click="undo">
          {{ t('manage_notifications.undo') }}
        </button>
      </div>

      <hr class="fh-rule" />

      <NotificationPreferencesTable
        :items="items"
        :saving="saving"
        :highlight="confirmedCategory"
        @change="onChange"
      />

      <p v-if="savedAt" class="fh-field-help saved">{{ t('common.saved') }}</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import {
  fetchSubscriptions,
  unsubscribeCategory,
  updateSubscriptions,
} from '@/api/notificationSubscriptions'
import NotificationPreferencesTable from '@/components/NotificationPreferencesTable.vue'
import type {
  NotificationCategory,
  NotificationChannel,
  PreferenceItem,
} from '@/types/api'

const route = useRoute()
const { t } = useI18n()

const token = computed(() => String(route.params.token))

const items = ref<PreferenceItem[]>([])
const displayName = ref('')
const loading = ref(true)
const saving = ref(false)
const errorCode = ref<string | null>(null)
const savedAt = ref<number | null>(null)

const confirmedCategory = ref<string | null>(null)
const previousChannel = ref<NotificationChannel | null>(null)

const confirmedLabel = computed(() => {
  if (!confirmedCategory.value) return ''
  const key = `notif_bell.cat.${confirmedCategory.value}`
  const label = t(key)
  return label === key ? confirmedCategory.value : label
})

interface AxiosLike { response?: { data?: { code?: string } } }
function codeOf(err: unknown): string {
  return (err as AxiosLike).response?.data?.code ?? 'generic'
}

function flashSaved() {
  savedAt.value = Date.now()
  setTimeout(() => {
    if (savedAt.value && Date.now() - savedAt.value > 1500) savedAt.value = null
  }, 1700)
}

async function load() {
  loading.value = true
  errorCode.value = null
  try {
    const { data } = await fetchSubscriptions(token.value)
    items.value = data.items
    displayName.value = data.display_name
  } catch (err) {
    errorCode.value = codeOf(err)
    return
  } finally {
    loading.value = false
  }

  // ?off=<category>: apply the one-click unsubscribe and confirm with Undo.
  // `one_click` is checked as well as `locked`: the operational alerts stay
  // changeable on this page but must not be switched off by following a link
  // from an email. Emails sent before that rule existed still carry an `?off=`
  // for them, so this lands on the preferences view instead of silently
  // disabling the alerting - the server refuses it either way.
  const off = route.query.off
  if (typeof off === 'string' && off) {
    const row = items.value.find((i) => i.category === off)
    // `!== false`, not a truthiness check: an absent flag means an older
    // backend (rolling update, or a cached bundle), and treating that as
    // "not allowed" would silently break the ?off= link for EVERY category.
    // Absent -> permitted, matching the field's server-side default; the
    // server refuses the operational ones regardless, and it is authoritative.
    if (row && !row.locked && row.one_click !== false && row.channel !== 'off') {
      await applyUnsubscribe(off)
    }
  }
}

async function applyUnsubscribe(category: string) {
  saving.value = true
  try {
    const { data } = await unsubscribeCategory(token.value, category)
    items.value = data.items
    confirmedCategory.value = data.category
    previousChannel.value = data.previous_channel
  } catch {
    /* a locked/unknown category just no-ops the confirmation */
  } finally {
    saving.value = false
  }
}

async function onChange(category: NotificationCategory, channel: NotificationChannel) {
  saving.value = true
  try {
    const { data } = await updateSubscriptions(token.value, { [category]: channel })
    items.value = data.items
    flashSaved()
  } finally {
    saving.value = false
  }
}

async function undo() {
  if (!confirmedCategory.value || !previousChannel.value) return
  const cat = confirmedCategory.value as NotificationCategory
  const chan = previousChannel.value
  confirmedCategory.value = null
  previousChannel.value = null
  await onChange(cat, chan)
}

onMounted(load)
</script>

<style scoped>
.manage-notifications {
  max-width: 640px;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-3) 0;
}

.intro {
  max-width: 60ch;
}

.confirm-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fh-space-3);
  background: var(--fh-accent-soft);
  border: var(--fh-border);
  border-left: 2px solid var(--fh-accent);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-2) var(--fh-space-3);
  margin-top: var(--fh-space-3);
}

.saved {
  color: var(--fh-success);
  margin-top: var(--fh-space-2);
}
</style>
