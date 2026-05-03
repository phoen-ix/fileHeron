<template>
  <div class="recipient-picker">
    <label class="fh-field-label" :for="inputId">{{ t('recipient.label') }}</label>

    <div class="chips-row" v-if="hasSelection">
      <span
        v-for="chip in chips"
        :key="`${chip.kind}-${chip.id}`"
        class="chip"
        :data-kind="chip.kind"
      >
        <span class="chip-icon" aria-hidden="true">
          {{ chip.kind === 'group' ? '◇' : '◆' }}
        </span>
        <span class="chip-label">{{ chip.label }}</span>
        <span v-if="chip.hint" class="chip-hint fh-mono">{{ chip.hint }}</span>
        <button
          type="button"
          class="chip-remove"
          :aria-label="t('recipient.remove')"
          :disabled="disabled"
          @click="removeChip(chip)"
        >
          ×
        </button>
      </span>
    </div>

    <div class="search-wrap">
      <input
        :id="inputId"
        v-model.trim="query"
        type="text"
        class="fh-field-input"
        :placeholder="t('recipient.search_placeholder')"
        autocomplete="off"
        :disabled="disabled"
        @focus="showResults = true"
        @blur="onBlur"
        @keydown.down.prevent="moveCursor(1)"
        @keydown.up.prevent="moveCursor(-1)"
        @keydown.enter.prevent="selectCursor"
        @keydown.escape="showResults = false"
      />
      <div
        v-if="showResults && (filteredUsers.length || filteredGroups.length || loading)"
        class="results"
        role="listbox"
      >
        <div v-if="loading" class="results-loading">{{ t('common.loading') }}</div>

        <div v-if="filteredUsers.length" class="results-section">
          <div class="section-eyebrow">{{ t('recipient.section_users') }}</div>
          <button
            v-for="(u, idx) in filteredUsers"
            :key="`u-${u.user_id}`"
            type="button"
            class="result-row"
            :class="{ active: cursorIdx === idx }"
            role="option"
            :aria-selected="cursorIdx === idx"
            @mousedown.prevent="addUser(u)"
            @mouseenter="cursorIdx = idx"
          >
            <span class="row-icon" aria-hidden="true">◆</span>
            <span class="row-name">{{ u.display_name }}</span>
            <span class="row-hint fh-mono">{{ u.email }}</span>
            <span class="row-role fh-mono">{{ u.role }}</span>
          </button>
        </div>

        <div v-if="filteredGroups.length" class="results-section">
          <div class="section-eyebrow">{{ t('recipient.section_groups') }}</div>
          <button
            v-for="(g, idx) in filteredGroups"
            :key="`g-${g.id}`"
            type="button"
            class="result-row"
            :class="{ active: cursorIdx === filteredUsers.length + idx }"
            role="option"
            :aria-selected="cursorIdx === filteredUsers.length + idx"
            @mousedown.prevent="addGroup(g)"
            @mouseenter="cursorIdx = filteredUsers.length + idx"
          >
            <span class="row-icon" aria-hidden="true">◇</span>
            <span class="row-name">{{ g.name }}</span>
            <span v-if="g.is_company_inbox" class="row-flag fh-mono">
              {{ t('recipient.inbox_flag') }}
            </span>
          </button>
        </div>

        <div
          v-if="!loading && !filteredUsers.length && !filteredGroups.length && query"
          class="results-empty"
        >
          {{ t('recipient.no_results') }}
        </div>
      </div>
    </div>

    <div v-if="errorMsg" class="fh-field-error">{{ errorMsg }}</div>
    <div v-else class="fh-field-help">{{ t('recipient.help_phase4') }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { listRecipientTargetGroups } from '@/api/groups'
import { searchUsers } from '@/api/users'
import { useApiError } from '@/composables/useApiError'
import type {
  GroupResponse,
  ShareRecipientsRequest,
  UserSearchItem,
} from '@/types/api'

const props = defineProps<{
  modelValue: ShareRecipientsRequest
  selectedUsers?: UserSearchItem[]
  selectedGroups?: GroupResponse[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: ShareRecipientsRequest]
  'update:selectedUsers': [users: UserSearchItem[]]
  'update:selectedGroups': [groups: GroupResponse[]]
}>()

const { t } = useI18n()
const { describe } = useApiError()
const inputId = `rp-${Math.random().toString(36).slice(2, 8)}`

const query = ref('')
const showResults = ref(false)
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const cursorIdx = ref(0)

// Local mirrors of the selected entities — needed so we can show their
// display_name / email / etc on the chips without re-fetching.
const selectedUsersLocal = ref<UserSearchItem[]>([...(props.selectedUsers ?? [])])
const selectedGroupsLocal = ref<GroupResponse[]>([...(props.selectedGroups ?? [])])

// Search results.
const allUserResults = ref<UserSearchItem[]>([])
const allGroupResults = ref<GroupResponse[]>([])

watch(
  () => props.selectedUsers,
  (v) => {
    if (v) selectedUsersLocal.value = [...v]
  },
)
watch(
  () => props.selectedGroups,
  (v) => {
    if (v) selectedGroupsLocal.value = [...v]
  },
)

const hasSelection = computed(
  () => selectedUsersLocal.value.length > 0 || selectedGroupsLocal.value.length > 0,
)

interface Chip {
  kind: 'user' | 'group'
  id: number
  label: string
  hint?: string
}

const chips = computed<Chip[]>(() => {
  const cs: Chip[] = []
  for (const u of selectedUsersLocal.value) {
    cs.push({
      kind: 'user',
      id: u.user_id,
      label: u.display_name,
      hint: u.email,
    })
  }
  for (const g of selectedGroupsLocal.value) {
    cs.push({ kind: 'group', id: g.id, label: g.name })
  }
  return cs
})

const filteredUsers = computed(() => {
  const selectedIds = new Set(selectedUsersLocal.value.map((u) => u.user_id))
  return allUserResults.value
    .filter((u) => !selectedIds.has(u.user_id))
    .slice(0, 6)
})

const filteredGroups = computed(() => {
  const selectedIds = new Set(selectedGroupsLocal.value.map((g) => g.id))
  const needle = query.value.toLowerCase().trim()
  return allGroupResults.value
    .filter((g) => !selectedIds.has(g.id))
    .filter((g) => !needle || g.name.toLowerCase().includes(needle))
    .slice(0, 6)
})

let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(query, (v) => {
  errorMsg.value = null
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    void doSearch(v)
  }, 180)
})

async function doSearch(q: string) {
  loading.value = true
  try {
    const { data } = await searchUsers(q)
    allUserResults.value = data.items
    cursorIdx.value = 0
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function loadInitialGroups() {
  try {
    const { data } = await listRecipientTargetGroups()
    allGroupResults.value = data.items
  } catch {
    /* non-fatal */
  }
}

function emitModel() {
  const value: ShareRecipientsRequest = {
    user_ids: selectedUsersLocal.value.map((u) => u.user_id),
    group_ids: selectedGroupsLocal.value.map((g) => g.id),
  }
  emit('update:modelValue', value)
  emit('update:selectedUsers', [...selectedUsersLocal.value])
  emit('update:selectedGroups', [...selectedGroupsLocal.value])
}

function addUser(u: UserSearchItem) {
  if (selectedUsersLocal.value.some((s) => s.user_id === u.user_id)) return
  selectedUsersLocal.value.push(u)
  query.value = ''
  showResults.value = false
  emitModel()
}

function addGroup(g: GroupResponse) {
  if (selectedGroupsLocal.value.some((s) => s.id === g.id)) return
  selectedGroupsLocal.value.push(g)
  query.value = ''
  showResults.value = false
  emitModel()
}

function removeChip(chip: Chip) {
  if (chip.kind === 'user') {
    selectedUsersLocal.value = selectedUsersLocal.value.filter(
      (u) => u.user_id !== chip.id,
    )
  } else {
    selectedGroupsLocal.value = selectedGroupsLocal.value.filter(
      (g) => g.id !== chip.id,
    )
  }
  emitModel()
}

function moveCursor(delta: number) {
  const total = filteredUsers.value.length + filteredGroups.value.length
  if (!total) return
  cursorIdx.value = (cursorIdx.value + delta + total) % total
  showResults.value = true
}

function selectCursor() {
  const usersLen = filteredUsers.value.length
  if (cursorIdx.value < usersLen) {
    addUser(filteredUsers.value[cursorIdx.value])
  } else {
    addGroup(filteredGroups.value[cursorIdx.value - usersLen])
  }
}

function onBlur() {
  // Delay so mousedown on a result row fires first.
  setTimeout(() => {
    showResults.value = false
  }, 120)
}

onMounted(() => {
  void loadInitialGroups()
  void doSearch('')
})
</script>

<style scoped>
.recipient-picker {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-1);
  margin-bottom: var(--fh-space-3);
}

.chips-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-2);
  margin: var(--fh-space-1) 0 var(--fh-space-2);
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-1);
  padding: 4px var(--fh-space-2);
  background: var(--fh-paper-raised);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  font-size: var(--fh-text-body-sm);
}

.chip[data-kind='group'] {
  background: var(--fh-accent-soft);
  border-color: rgba(180, 83, 9, 0.3);
}

.chip-icon {
  color: var(--fh-accent);
  font-size: 12px;
}

.chip-label {
  color: var(--fh-ink);
}

.chip-hint {
  color: var(--fh-subtle);
  font-size: var(--fh-text-mono-sm);
}

.chip-remove {
  background: none;
  border: none;
  font-size: 16px;
  color: var(--fh-subtle);
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
}

.chip-remove:hover {
  color: var(--fh-danger);
}

.search-wrap {
  position: relative;
}

.results {
  position: absolute;
  z-index: 30;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background: var(--fh-paper-raised);
  border: var(--fh-border-strong);
  border-radius: var(--fh-radius-sm);
  max-height: 320px;
  overflow-y: auto;
  box-shadow: 0 4px 24px rgba(26, 29, 36, 0.06);
}

.results-loading,
.results-empty {
  padding: var(--fh-space-2) var(--fh-space-3);
  color: var(--fh-subtle);
  font-size: var(--fh-text-body-sm);
}

.section-eyebrow {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--fh-subtle);
  padding: var(--fh-space-2) var(--fh-space-3) var(--fh-space-1);
  border-top: var(--fh-border);
}

.results-section:first-child .section-eyebrow {
  border-top: none;
}

.result-row {
  width: 100%;
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  gap: var(--fh-space-2);
  align-items: baseline;
  padding: var(--fh-space-2) var(--fh-space-3);
  background: transparent;
  border: none;
  font: inherit;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.result-row.active,
.result-row:hover {
  background: var(--fh-paper-sunk);
}

.row-icon {
  color: var(--fh-accent);
  font-size: 12px;
}

.row-name {
  color: var(--fh-ink);
}

.row-hint,
.row-role,
.row-flag {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.row-flag {
  color: var(--fh-accent);
}
</style>
