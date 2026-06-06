<script setup lang="ts">
/* /admin/groups/:id - group detail with member management. */
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import {
  addMembers,
  deleteGroup,
  getGroup,
  removeMember,
  updateGroup,
} from '@/api/groups'
import { searchUsers } from '@/api/users'
import { useApiError } from '@/composables/useApiError'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import type { GroupDetailResponse, UserSearchItem } from '@/types/api'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const { t } = useI18n()
const { formatDateOnly: formatDate } = useSiteDateFormat()
const { describe } = useApiError()

const group = ref<GroupDetailResponse | null>(null)
const loading = ref(true)
const errorMsg = ref<string | null>(null)

const editingName = ref(false)
const editName = ref('')
const editDescription = ref('')
const editIsInbox = ref(false)
const savingEdit = ref(false)

// member-add typeahead
const userQuery = ref('')
const userResults = ref<UserSearchItem[]>([])
const userSearching = ref(false)
const adding = ref(false)

async function load() {
  const id = Number(route.params.id)
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getGroup(id)
    group.value = data
    editName.value = data.name
    editDescription.value = data.description || ''
    editIsInbox.value = data.is_company_inbox
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onSaveEdit() {
  if (!group.value) return
  savingEdit.value = true
  try {
    await updateGroup(group.value.id, {
      name: editName.value.trim(),
      description: editDescription.value.trim() || null,
      is_company_inbox: editIsInbox.value,
    })
    editingName.value = false
    ui.pushToast(t('admin_group_detail.saved_toast'), 'success')
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    savingEdit.value = false
  }
}

let searchTimer: ReturnType<typeof setTimeout> | null = null

function onUserQuery() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(async () => {
    userSearching.value = true
    try {
      const { data } = await searchUsers(userQuery.value)
      userResults.value = data.items
    } finally {
      userSearching.value = false
    }
  }, 180)
}

async function onAdd(u: UserSearchItem) {
  if (!group.value) return
  adding.value = true
  try {
    await addMembers(group.value.id, [u.user_id])
    userQuery.value = ''
    userResults.value = []
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    adding.value = false
  }
}

async function onRemove(userId: number) {
  if (!group.value) return
  if (!(await ui.confirm({ message: t('admin_group_detail.remove_confirm'), danger: true }))) return
  try {
    await removeMember(group.value.id, userId)
    await load()
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

async function onDeleteGroup() {
  if (!group.value) return
  if (!(await ui.confirm({ message: t('admin_group_detail.delete_confirm'), danger: true }))) return
  try {
    await deleteGroup(group.value.id)
    ui.pushToast(t('admin_group_detail.deleted_toast'), 'success')
    await router.push({ name: 'admin-groups' })
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  }
}

onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="group">
      <span class="fh-eyebrow">{{ t('admin_group_detail.eyebrow') }}</span>

      <div class="title-row">
        <h1 class="fh-display-md">{{ group.name }}</h1>
        <span v-if="group.is_company_inbox" class="fh-pill" data-state="warn">
          {{ t('recipient.inbox_flag') }}
        </span>
      </div>

      <p v-if="group.description" class="description">{{ group.description }}</p>

      <button
        v-if="!editingName"
        type="button"
        class="fh-btn-text"
        @click="editingName = true"
      >
        {{ t('admin_group_detail.edit') }}
      </button>

      <form v-else class="edit-form" @submit.prevent="onSaveEdit">
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_groups.name_label') }}</span>
          <input v-model.trim="editName" class="fh-field-input" required />
        </label>
        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_groups.description_label') }}</span>
          <textarea v-model.trim="editDescription" class="fh-field-input desc-input" rows="2" />
        </label>
        <label class="checkbox">
          <input v-model="editIsInbox" type="checkbox" />
          <span>
            <span class="cb-label">{{ t('admin_groups.is_inbox_label') }}</span>
            <span class="cb-help">{{ t('admin_groups.is_inbox_help') }}</span>
          </span>
        </label>
        <div class="form-actions">
          <button type="submit" class="fh-btn" :disabled="savingEdit">
            {{ savingEdit ? t('common.loading') : t('common.save') }}
          </button>
          <button type="button" class="fh-btn-text" @click="editingName = false">
            {{ t('common.cancel') }}
          </button>
        </div>
      </form>

      <hr class="fh-rule" />

      <h2 class="section-h2">
        {{ t('admin_group_detail.members_heading', { n: group.member_count }) }}
      </h2>

      <div class="add-member">
        <input
          v-model.trim="userQuery"
          type="text"
          class="fh-field-input"
          :placeholder="t('admin_group_detail.add_placeholder')"
          @input="onUserQuery"
          @focus="onUserQuery"
        />
        <ul v-if="userResults.length > 0 || userSearching" class="add-results">
          <li v-if="userSearching" class="add-result-loading">
            {{ t('common.loading') }}
          </li>
          <li
            v-for="u in userResults.filter((r) => !(group?.members ?? []).some((m) => m.user_id === r.user_id))"
            :key="u.user_id"
            class="add-result"
          >
            <span class="ar-name">{{ u.display_name }}</span>
            <span class="ar-hint fh-mono">{{ u.email }}</span>
            <span class="ar-role fh-mono">{{ u.role }}</span>
            <button
              type="button"
              class="fh-btn-text"
              :disabled="adding"
              @click="onAdd(u)"
            >
              {{ t('admin_group_detail.add') }}
            </button>
          </li>
        </ul>
      </div>

      <ul class="member-list">
        <li v-for="m in group.members" :key="m.user_id" class="member-row">
          <div class="member-name">{{ m.display_name }}</div>
          <div class="member-meta">
            <span class="fh-mono">{{ m.email }}</span>
            <span class="fh-mono role">{{ m.role }}</span>
            <span class="fh-mono since">
              {{ t('admin_group_detail.since', { d: formatDate(m.joined_at) }) }}
            </span>
          </div>
          <button
            type="button"
            class="fh-btn-text danger"
            @click="onRemove(m.user_id)"
          >
            {{ t('admin_group_detail.remove') }}
          </button>
        </li>
      </ul>

      <hr class="fh-rule" />

      <div class="danger-zone">
        <h2 class="section-h2">{{ t('admin_group_detail.danger_heading') }}</h2>
        <p class="fh-field-help">{{ t('admin_group_detail.danger_help') }}</p>
        <button type="button" class="fh-btn fh-btn-danger" @click="onDeleteGroup">
          {{ t('admin_group_detail.delete') }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}

.title-row {
  display: flex;
  align-items: baseline;
  gap: var(--fh-space-3);
  margin: var(--fh-space-1) 0;
}

.description {
  color: var(--fh-subtle);
  margin: var(--fh-space-2) 0;
}

.edit-form {
  margin: var(--fh-space-3) 0;
  padding: var(--fh-space-3);
  background: var(--fh-paper-raised);
  border: var(--fh-border);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.desc-input {
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
}

.cb-help {
  display: block;
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
}

.form-actions {
  display: flex;
  gap: var(--fh-space-3);
}

.section-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  margin: var(--fh-space-3) 0;
}

.add-member {
  position: relative;
  margin-bottom: var(--fh-space-3);
}

.add-results {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  position: absolute;
  z-index: 20;
  left: 0;
  right: 0;
  background: var(--fh-paper-raised);
  border: var(--fh-border-strong);
  border-radius: var(--fh-radius-sm);
  max-height: 320px;
  overflow-y: auto;
  box-shadow: 0 4px 24px rgba(26, 29, 36, 0.06);
}

.add-result-loading {
  padding: var(--fh-space-2) var(--fh-space-3);
  color: var(--fh-subtle);
}

.add-result {
  display: grid;
  grid-template-columns: 1fr auto auto auto;
  gap: var(--fh-space-3);
  align-items: baseline;
  padding: var(--fh-space-2) var(--fh-space-3);
  border-bottom: var(--fh-border);
}

.add-result:last-child {
  border-bottom: none;
}

.ar-name {
  color: var(--fh-ink);
}

.ar-hint,
.ar-role {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.member-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: var(--fh-border);
}

.member-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) auto;
  gap: var(--fh-space-3);
  align-items: center;
  padding: var(--fh-space-3) 0;
  border-bottom: var(--fh-border);
}

.member-name {
  color: var(--fh-ink);
}

.member-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-3);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.fh-btn-text.danger {
  color: var(--fh-danger);
}

.danger-zone {
  margin-top: var(--fh-space-4);
}

@media (max-width: 720px) {
  .member-row,
  .add-result {
    grid-template-columns: 1fr;
  }
}
</style>
