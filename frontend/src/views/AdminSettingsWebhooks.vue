<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  createWebhook,
  deleteWebhook,
  getWebhookEvents,
  listWebhookDeliveries,
  listWebhooks,
  retryWebhookDelivery,
  testWebhook,
  updateWebhook,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type { WebhookDeliveryItem, WebhookItem } from '@/types/api'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const items = ref<WebhookItem[]>([])
const events = ref<string[]>([])
const loading = ref(true)
const errorMsg = ref<string | null>(null)

// Create form.
const newName = ref('')
const newUrl = ref('')
const newEvents = reactive<Record<string, boolean>>({})
const creating = ref(false)
const justCreatedSecret = ref<string | null>(null)

const allEventsSelected = computed(
  () => events.value.length > 0 && events.value.every((e) => newEvents[e]),
)
function toggleAllEvents() {
  const next = !allEventsSelected.value
  events.value.forEach((e) => (newEvents[e] = next))
}

// Per-webhook deliveries panel.
const openDeliveries = ref<number | null>(null)
const deliveries = ref<WebhookDeliveryItem[]>([])
const deliveriesLoading = ref(false)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const [{ data: ws }, { data: ev }] = await Promise.all([listWebhooks(), getWebhookEvents()])
    items.value = ws
    events.value = ev.events
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onCreate() {
  const selected = events.value.filter((e) => newEvents[e])
  if (!newName.value.trim() || !newUrl.value.trim() || selected.length === 0) {
    ui.pushToast(t('admin_webhooks.create_incomplete'), 'error')
    return
  }
  creating.value = true
  try {
    const { data } = await createWebhook({
      name: newName.value.trim(),
      url: newUrl.value.trim(),
      event_types: selected,
    })
    justCreatedSecret.value = data.secret
    newName.value = ''
    newUrl.value = ''
    events.value.forEach((e) => (newEvents[e] = false))
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    creating.value = false
  }
}

async function onToggleActive(w: WebhookItem) {
  try {
    await updateWebhook(w.id, { active: !w.active })
    w.active = !w.active
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

async function onTest(w: WebhookItem) {
  try {
    await testWebhook(w.id)
    ui.pushToast(t('admin_webhooks.test_queued'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

async function onDelete(w: WebhookItem) {
  if (!(await ui.confirm({ message: t('admin_webhooks.delete_confirm', { name: w.name }), danger: true }))) return
  try {
    await deleteWebhook(w.id)
    items.value = items.value.filter((x) => x.id !== w.id)
    if (openDeliveries.value === w.id) openDeliveries.value = null
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

async function toggleDeliveries(w: WebhookItem) {
  if (openDeliveries.value === w.id) {
    openDeliveries.value = null
    return
  }
  openDeliveries.value = w.id
  deliveriesLoading.value = true
  deliveries.value = []
  try {
    const { data } = await listWebhookDeliveries(w.id)
    deliveries.value = data
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    deliveriesLoading.value = false
  }
}

async function onRetry(d: WebhookDeliveryItem) {
  try {
    await retryWebhookDelivery(d.id)
    ui.pushToast(t('admin_webhooks.retry_queued'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

function copySecret() {
  if (justCreatedSecret.value) void navigator.clipboard?.writeText(justCreatedSecret.value)
}

const pillTone: Record<string, 'active' | 'warn' | 'danger'> = {
  sent: 'active',
  pending: 'warn',
  failed: 'danger',
}

// Friendly label for an event id; falls back to the raw id for any event the
// backend adds before it's translated (vue-i18n returns the key path on miss).
function eventLabel(e: string): string {
  const key = `admin_webhooks.event.${e.replace(/\./g, '_')}`
  const label = t(key)
  return label === key ? e : label
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <span class="fh-eyebrow">{{ t('admin_webhooks.eyebrow') }}</span>
    <h1 class="fh-h1">{{ t('admin_webhooks.title') }}</h1>
    <p class="fh-field-help intro">{{ t('admin_webhooks.intro') }}</p>

    <hr class="fh-rule" />

    <!-- One-time secret reveal -->
    <div v-if="justCreatedSecret" class="created-box fh-rise">
      <div class="created-eyebrow">{{ t('admin_webhooks.secret_created') }}</div>
      <p class="warning">{{ t('admin_webhooks.secret_warning') }}</p>
      <pre class="secret fh-mono">{{ justCreatedSecret }}</pre>
      <div class="actions">
        <button type="button" class="fh-btn-text" @click="copySecret">{{ t('admin_webhooks.copy') }}</button>
        <button type="button" class="fh-btn-text" @click="justCreatedSecret = null">{{ t('admin_webhooks.acknowledged') }}</button>
      </div>
    </div>

    <!-- Create -->
    <section class="card create-form">
      <h2 class="card-h2">{{ t('admin_webhooks.add') }}</h2>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_webhooks.name') }}</span>
        <input v-model="newName" class="fh-field-input" :placeholder="t('admin_webhooks.name_ph')" />
      </label>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_webhooks.url') }}</span>
        <input v-model="newUrl" class="fh-field-input fh-mono" type="url" placeholder="https://example.com/hook" />
      </label>
      <div class="fh-field">
        <div class="events-head">
          <span class="fh-field-label">{{ t('admin_webhooks.events') }}</span>
          <button v-if="events.length" type="button" class="fh-btn-text" @click="toggleAllEvents">
            {{ allEventsSelected ? t('admin_webhooks.deselect_all') : t('admin_webhooks.select_all') }}
          </button>
        </div>
        <div class="events-grid">
          <label v-for="e in events" :key="e" class="event-check" :title="e">
            <input v-model="newEvents[e]" type="checkbox" />
            <span>{{ eventLabel(e) }}</span>
          </label>
        </div>
      </div>
      <div class="actions">
        <button type="button" class="fh-btn" :disabled="creating" @click="onCreate">
          {{ creating ? t('common.loading') : t('admin_webhooks.create') }}
        </button>
      </div>
    </section>

    <!-- List -->
    <div v-if="loading" class="fh-notice">{{ t('common.loading') }}</div>
    <div
v-else-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>
    <p v-else-if="items.length === 0" class="fh-field-help empty">{{ t('admin_webhooks.none') }}</p>

    <ul v-else class="hooks">
      <li v-for="w in items" :key="w.id" class="hook card">
        <div class="hook-head">
          <div class="hook-id">
            <span class="hook-name">{{ w.name }}</span>
            <span class="fh-pill" :data-state="w.active ? 'active' : undefined">
              {{ w.active ? t('admin_webhooks.on') : t('admin_webhooks.off') }}
            </span>
          </div>
          <div class="hook-actions">
            <button type="button" class="fh-btn-text" @click="onTest(w)">{{ t('admin_webhooks.test') }}</button>
            <button type="button" class="fh-btn-text" @click="toggleDeliveries(w)">{{ t('admin_webhooks.deliveries') }}</button>
            <button type="button" class="fh-btn-text" @click="onToggleActive(w)">
              {{ w.active ? t('admin_webhooks.disable') : t('admin_webhooks.enable') }}
            </button>
            <button type="button" class="fh-btn-text danger" @click="onDelete(w)">{{ t('admin_webhooks.delete') }}</button>
          </div>
        </div>
        <pre class="hook-url fh-mono">{{ w.url }}</pre>
        <div class="hook-events">
          <span v-for="e in w.event_types" :key="e" class="event-tag" :title="e">{{ eventLabel(e) }}</span>
        </div>

        <div v-if="openDeliveries === w.id" class="deliveries">
          <div v-if="deliveriesLoading" class="fh-field-help">{{ t('common.loading') }}</div>
          <p v-else-if="deliveries.length === 0" class="fh-field-help">{{ t('admin_webhooks.no_deliveries') }}</p>
          <table v-else class="data-table">
            <thead>
              <tr>
                <th>{{ t('admin_webhooks.col_event') }}</th>
                <th>{{ t('admin_webhooks.col_status') }}</th>
                <th class="num">{{ t('admin_webhooks.col_code') }}</th>
                <th class="num">{{ t('admin_webhooks.col_attempts') }}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in deliveries" :key="d.id">
                <td :title="d.event_type">{{ eventLabel(d.event_type) }}</td>
                <td><span class="fh-pill" :data-state="pillTone[d.status]">{{ d.status }}</span></td>
                <td class="num fh-mono">{{ d.response_code ?? '-' }}</td>
                <td class="num fh-mono">{{ d.attempts }}</td>
                <td class="num">
                  <button type="button" class="fh-btn-text" @click="onRetry(d)">{{ t('admin_webhooks.retry') }}</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.intro {
  max-width: 64ch;
  margin: 0 0 var(--fh-space-2);
}
.card {
  background: var(--fh-paper-raised);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-4);
  margin-top: var(--fh-space-4);
}
.card-h2 {
  margin: 0 0 var(--fh-space-3);
  font-family: var(--fh-font-display);
  font-size: 1.3rem;
  font-weight: 400;
}
.create-form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-3);
}
.events-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fh-space-2);
}
.events-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--fh-space-2);
  margin-top: var(--fh-space-1);
}
.event-check {
  display: flex;
  gap: var(--fh-space-2);
  align-items: center;
}
.actions {
  display: flex;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-1);
}
.created-box {
  background: var(--fh-accent-soft);
  border: var(--fh-border);
  border-left: 2px solid var(--fh-accent);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-4);
  margin-top: var(--fh-space-4);
}
.created-eyebrow {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--fh-subtle);
}
.secret {
  background: var(--fh-paper);
  padding: var(--fh-space-3);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  word-break: break-all;
  margin: var(--fh-space-2) 0;
  user-select: all;
}
.hooks {
  list-style: none;
  margin: 0;
  padding: 0;
}
.hook-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--fh-space-3);
  flex-wrap: wrap;
}
.hook-id {
  display: flex;
  align-items: center;
  gap: var(--fh-space-2);
}
.hook-name {
  font-size: 1.1rem;
}
.hook-actions {
  display: flex;
  gap: var(--fh-space-3);
  flex-wrap: wrap;
}
.hook-url {
  background: var(--fh-paper);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-2) var(--fh-space-3);
  margin: var(--fh-space-2) 0;
  word-break: break-all;
}
.hook-events {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-1);
}
.event-tag {
  font-size: var(--fh-text-mono-sm);
  background: var(--fh-paper);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  padding: 1px 6px;
  color: var(--fh-subtle);
}
.deliveries {
  margin-top: var(--fh-space-3);
  border-top: var(--fh-border);
  padding-top: var(--fh-space-3);
}
.data-table {
  width: 100%;
  border-collapse: collapse;
}
.data-table th {
  text-align: left;
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
  font-weight: 400;
  padding: 4px 8px 4px 0;
}
.data-table td {
  padding: 4px 8px 4px 0;
  border-top: 1px solid var(--fh-hairline);
}
.data-table .num {
  text-align: right;
}
.fh-btn-text.danger {
  color: var(--fh-danger, #b00020);
}
.empty {
  margin-top: var(--fh-space-4);
}
</style>
