<script setup lang="ts">
/* /admin/groups — list + create groups (admin only). */
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { createGroup, listGroups } from '@/api/groups'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type { GroupResponse } from '@/types/api'
import { formatDateInSiteTime } from '@/utils/datetime'

const router = useRouter()
const ui = useUiStore()
const { t, locale } = useI18n()
const { describe } = useApiError()

function formatDate(iso: string | null): string {
  return formatDateInSiteTime(iso, locale.value)
}

const items = ref<GroupResponse[]>([])
const loading = ref(true)
const errorMsg = ref<string | null>(null)

const showForm = ref(false)
const newName = ref('')
const newDescription = ref('')
const newIsInbox = ref(false)
const creating = ref(false)
const formError = ref<string | null>(null)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await listGroups()
    items.value = data.items
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onCreate() {
  formError.value = null
  creating.value = true
  try {
    await createGroup({
      name: newName.value.trim(),
      description: newDescription.value.trim() || null,
      is_company_inbox: newIsInbox.value,
    })
    showForm.value = false
    newName.value = ''
    newDescription.value = ''
    newIsInbox.value = false
    ui.pushToast(t('admin_groups.created_toast'), 'success')
    await load()
  } catch (err) {
    formError.value = describe(err)
  } finally {
    creating.value = false
  }
}

function open(g: GroupResponse) {
  router.push({ name: 'admin-group-detail', params: { id: g.id } })
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div class="header-row">
      <div>
        <span class="fh-eyebrow">{{ t('admin_groups.eyebrow') }}</span>
      </div>
      <button v-if="!showForm" type="button" class="fh-btn" @click="showForm = true">
        {{ t('admin_groups.new_group') }} <span aria-hidden="true">→</span>
      </button>
    </div>

    <hr class="fh-rule" />

    <form v-if="showForm" class="create-form" @submit.prevent="onCreate">
      <h2 class="form-h2">{{ t('admin_groups.create_heading') }}</h2>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_groups.name_label') }}</span>
        <input
          v-model.trim="newName"
          class="fh-field-input"
          type="text"
          maxlength="120"
          required
          :placeholder="t('admin_groups.name_placeholder')"
        />
      </label>
      <label class="fh-field">
        <span class="fh-field-label">{{ t('admin_groups.description_label') }}</span>
        <textarea
          v-model.trim="newDescription"
          class="fh-field-input message-input"
          rows="2"
          maxlength="4000"
        />
      </label>
      <label class="checkbox">
        <input v-model="newIsInbox" type="checkbox" />
        <span>
          <span class="cb-label">{{ t('admin_groups.is_inbox_label') }}</span>
          <span class="cb-help">{{ t('admin_groups.is_inbox_help') }}</span>
        </span>
      </label>
      <div v-if="formError" class="fh-notice" data-tone="error">{{ formError }}</div>
      <div class="form-actions">
        <button type="submit" class="fh-btn" :disabled="creating || !newName">
          {{ creating ? t('common.loading') : t('admin_groups.create_submit') }}
        </button>
        <button type="button" class="fh-btn-text" @click="showForm = false">
          {{ t('common.cancel') }}
        </button>
      </div>
    </form>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

    <table v-else-if="items.length > 0" class="group-table">
      <thead>
        <tr>
          <th>{{ t('admin_groups.col.name') }}</th>
          <th>{{ t('admin_groups.col.kind') }}</th>
          <th>{{ t('admin_groups.col.members') }}</th>
          <th>{{ t('admin_groups.col.created') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="g in items"
          :key="g.id"
          tabindex="0"
          @click="open(g)"
          @keydown.enter="open(g)"
        >
          <td>
            <div class="row-name">{{ g.name }}</div>
            <div v-if="g.description" class="row-desc">{{ g.description }}</div>
          </td>
          <td>
            <span v-if="g.is_company_inbox" class="fh-pill" data-state="warn">
              {{ t('recipient.inbox_flag') }}
            </span>
            <span v-else class="fh-mono row-plain">{{ t('admin_groups.kind_normal') }}</span>
          </td>
          <td class="numeric fh-mono">{{ g.member_count }}</td>
          <td class="fh-mono">{{ formatDate(g.created_at) }}</td>
        </tr>
      </tbody>
    </table>

    <div v-else class="empty-state">
      <p class="fh-display-md">{{ t('admin_groups.empty_title') }}</p>
      <p class="fh-field-help">{{ t('admin_groups.empty_subtitle') }}</p>
    </div>
  </div>
</template>

<style scoped>
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: var(--fh-space-4);
}

.create-form {
  background: var(--fh-paper-raised);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-4);
  margin-bottom: var(--fh-space-5);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.form-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.25rem;
  margin: 0 0 var(--fh-space-2);
}

.message-input {
  resize: vertical;
  font-family: inherit;
  line-height: 1.5;
}

.checkbox {
  display: flex;
  gap: var(--fh-space-2);
  align-items: flex-start;
  padding: var(--fh-space-2) 0;
}

.cb-label {
  display: block;
  color: var(--fh-ink);
}

.cb-help {
  display: block;
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
}

.form-actions {
  display: flex;
  gap: var(--fh-space-3);
  align-items: center;
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}

.group-table {
  width: 100%;
  border-collapse: collapse;
}

.group-table th {
  text-align: left;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
  font-weight: 500;
  padding: var(--fh-space-2) var(--fh-space-3) var(--fh-space-2) 0;
  border-bottom: var(--fh-border);
}

.group-table th.numeric,
.group-table td.numeric {
  text-align: right;
}

.group-table tbody tr {
  cursor: pointer;
  transition: background var(--fh-duration-fast) var(--fh-easing);
}

.group-table tbody tr:hover {
  background: var(--fh-paper-raised);
}

.group-table tbody tr:focus-visible {
  outline: 2px solid var(--fh-focus-ring);
  outline-offset: -2px;
}

.group-table td {
  padding: var(--fh-space-3) var(--fh-space-3) var(--fh-space-3) 0;
  border-bottom: var(--fh-border);
  vertical-align: middle;
}

.row-name {
  color: var(--fh-ink);
}

.row-desc {
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
  margin-top: 2px;
}

.row-plain {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.empty-state {
  padding: var(--fh-space-6) 0;
  text-align: center;
}
</style>
