<template>
  <div class="recipient-picker">
    <label class="fh-field-label" :for="inputId">{{ t('recipient.label') }}</label>

    <div v-if="hasSelection" class="chips-row">
      <span
        v-for="chip in chips"
        :key="`${chip.kind}-${chip.id}`"
        class="chip"
        :data-kind="chip.kind"
      >
        <span class="chip-icon" aria-hidden="true">
          {{ chip.kind === 'group' ? '◇' : chip.kind === 'email' ? '↗' : '◆' }}
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
        role="combobox"
        aria-autocomplete="list"
        :aria-expanded="showResults && optionCount > 0"
        :aria-controls="`${inputId}-listbox`"
        :aria-activedescendant="activeOptionId"
        @focus="showResults = true"
        @blur="onBlur"
        @keydown.down.prevent="moveCursor(1)"
        @keydown.up.prevent="moveCursor(-1)"
        @keydown.enter.prevent="selectCursor"
        @keydown.escape="showResults = false"
      />
      <!-- Open whenever there is something to say, INCLUDING "nothing matched":
           the list used to render only when it had rows, so its own
           "No matches." line could never appear and a typed address that
           matched nobody looked exactly like a picked one. -->
      <div
        v-if="showResults && (optionCount > 0 || loading || (query && settled))"
        :id="`${inputId}-listbox`"
        class="results"
        role="listbox"
      >
        <div v-if="loading" class="results-loading">{{ t('common.loading') }}</div>

        <div v-if="filteredUsers.length" class="results-section">
          <div class="section-eyebrow">{{ t('recipient.section_users') }}</div>
          <button
            v-for="(u, idx) in filteredUsers"
            :id="`${inputId}-opt-${idx}`"
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
            <span class="fh-sr-only">{{ t('recipient.section_users') }}</span>
            <span class="row-name">{{ u.display_name }}</span>
            <span class="row-hint fh-mono">{{ u.email }}</span>
            <span class="row-role fh-mono">{{ u.role }}</span>
          </button>
        </div>

        <div v-if="filteredGroups.length" class="results-section">
          <div class="section-eyebrow">{{ t('recipient.section_groups') }}</div>
          <button
            v-for="(g, idx) in filteredGroups"
            :id="`${inputId}-opt-${filteredUsers.length + idx}`"
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

        <div v-if="externalOption" class="results-section">
          <div class="section-eyebrow">{{ t('recipient.section_external') }}</div>
          <button
            :id="`${inputId}-opt-${externalIdx}`"
            type="button"
            class="result-row"
            :class="{ active: cursorIdx === externalIdx }"
            role="option"
            :aria-selected="cursorIdx === externalIdx"
            data-testid="external-option"
            @mousedown.prevent="addEmail(externalOption)"
            @mouseenter="cursorIdx = externalIdx"
          >
            <span class="row-icon" aria-hidden="true">↗</span>
            <span class="row-name">{{
              t('recipient.external_option', { email: externalOption })
            }}</span>
          </button>
        </div>

        <div v-if="settled && optionCount === 0 && query" class="results-empty">
          {{ noAccountFor ? noAccountMessage : t('recipient.no_results') }}
        </div>
      </div>
    </div>

    <div v-if="errorMsg" class="fh-field-error">{{ errorMsg }}</div>
    <!-- Typed but never picked: the text in the box is not a recipient, and the
         form's submit stays disabled because of it. Say so where the text is. -->
    <div
      v-else-if="pendingMessage"
      class="fh-field-error"
      role="status"
      data-testid="recipient-pending"
    >
      {{ pendingMessage }}
    </div>
    <div v-else class="fh-field-help">{{ t('recipient.help_phase4') }}</div>
  </div>
</template>

<script setup lang="ts">
  import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { listRecipientTargetGroups } from '@/api/groups'
  import { searchUsers } from '@/api/users'
  import { useApiError } from '@/composables/useApiError'
  import type { GroupResponse, ShareRecipientsRequest, UserSearchItem } from '@/types/api'

  const props = defineProps<{
    modelValue: ShareRecipientsRequest
    selectedUsers?: UserSearchItem[]
    selectedGroups?: GroupResponse[]
    disabled?: boolean
    /** Offer "send a download link to <address>" for an address that matches
     *  no one (`/me.can_share_external`). */
    allowExternal?: boolean
    /** The sender may attach a public link - the no-account message points at
     *  it when external sending itself is not offered. */
    canPublicLink?: boolean
  }>()

  const emit = defineEmits<{
    'update:modelValue': [value: ShareRecipientsRequest]
    'update:selectedUsers': [users: UserSearchItem[]]
    'update:selectedGroups': [groups: GroupResponse[]]
    /** The search text that has NOT become a recipient ('' when none). */
    'update:pending': [text: string]
  }>()

  const { t } = useI18n()
  const { describe } = useApiError()
  const inputId = `rp-${Math.random().toString(36).slice(2, 8)}`

  /** The option the arrow keys have landed on, for `aria-activedescendant`.
   *  Without it the highlight moved visually and the screen reader said nothing
   *  on any keystroke - on the primary share flow (audit #2). */
  const activeOptionId = computed(() =>
    cursorIdx.value >= 0 ? `${inputId}-opt-${cursorIdx.value}` : undefined,
  )

  const query = ref('')
  const showResults = ref(false)
  const loading = ref(false)
  const errorMsg = ref<string | null>(null)
  const cursorIdx = ref(0)

  // Local mirrors of the selected entities - needed so we can show their
  // display_name / email / etc on the chips without re-fetching.
  const selectedUsersLocal = ref<UserSearchItem[]>([...(props.selectedUsers ?? [])])
  const selectedGroupsLocal = ref<GroupResponse[]>([...(props.selectedGroups ?? [])])
  const selectedEmailsLocal = ref<string[]>([...(props.modelValue.emails ?? [])])

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
    () =>
      selectedUsersLocal.value.length > 0 ||
      selectedGroupsLocal.value.length > 0 ||
      selectedEmailsLocal.value.length > 0,
  )

  interface Chip {
    kind: 'user' | 'group' | 'email'
    id: number | string
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
    for (const e of selectedEmailsLocal.value) {
      cs.push({ kind: 'email', id: e, label: e, hint: t('recipient.external_chip_hint') })
    }
    return cs
  })

  const filteredUsers = computed(() => {
    const selectedIds = new Set(selectedUsersLocal.value.map((u) => u.user_id))
    return allUserResults.value.filter((u) => !selectedIds.has(u.user_id)).slice(0, 6)
  })

  const filteredGroups = computed(() => {
    const selectedIds = new Set(selectedGroupsLocal.value.map((g) => g.id))
    const needle = query.value.toLowerCase().trim()
    return allGroupResults.value
      .filter((g) => !selectedIds.has(g.id))
      .filter((g) => !needle || g.name.toLowerCase().includes(needle))
      .slice(0, 6)
  })

  // Same shape the backend's EmailLike accepts, so an offered address is never
  // one the server then refuses as malformed.
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

  /** The query as an address, or null when it does not look like one. */
  const queryEmail = computed(() => {
    const q = query.value.trim().toLowerCase()
    return EMAIL_RE.test(q) ? q : null
  })

  /** The query the current results answer. Until the debounced search for the
   *  text in the box has come back, the list is stale, and deciding "nobody has
   *  this address" from it would offer a link to someone about to appear. */
  const searchedQuery = ref<string | null>(null)
  const settled = computed(() => !loading.value && searchedQuery.value === query.value)

  const queryMatchesSomeone = computed(() => {
    const email = queryEmail.value
    if (!email) return false
    return (
      allUserResults.value.some((u) => u.email.toLowerCase() === email) ||
      selectedUsersLocal.value.some((u) => u.email.toLowerCase() === email)
    )
  })

  /** The address to offer a download link to, or null. */
  const externalOption = computed(() => {
    const email = queryEmail.value
    if (!props.allowExternal || !email || !settled.value) return null
    if (queryMatchesSomeone.value || selectedEmailsLocal.value.includes(email)) return null
    return email
  })

  /** An address that matches no one the sender can reach, with no link offer. */
  const noAccountFor = computed(() => {
    const email = queryEmail.value
    if (props.allowExternal || !email || !settled.value) return null
    return queryMatchesSomeone.value ? null : email
  })

  const noAccountMessage = computed(() => {
    if (!noAccountFor.value) return ''
    const base = t('recipient.no_account', { email: noAccountFor.value })
    return props.canPublicLink ? `${base} ${t('recipient.no_account_public_link')}` : base
  })

  const optionCount = computed(
    () => filteredUsers.value.length + filteredGroups.value.length + (externalOption.value ? 1 : 0),
  )
  const externalIdx = computed(() => filteredUsers.value.length + filteredGroups.value.length)

  const pendingMessage = computed(() => {
    if (!query.value || showResults.value) return ''
    return noAccountFor.value
      ? noAccountMessage.value
      : t('recipient.not_added', { q: query.value })
  })

  let searchTimer: ReturnType<typeof setTimeout> | null = null

  watch(query, (v) => {
    emit('update:pending', v)
    errorMsg.value = null
    if (searchTimer) clearTimeout(searchTimer)
    searchTimer = setTimeout(() => {
      void doSearch(v)
    }, 180)
  })

  // A slower answer for "ann" must not overwrite the one for "annabelle".
  let searchSeq = 0

  async function doSearch(q: string) {
    const seq = ++searchSeq
    loading.value = true
    try {
      const { data } = await searchUsers(q)
      if (seq !== searchSeq) return
      allUserResults.value = data.items
      searchedQuery.value = q
      cursorIdx.value = 0
    } catch (err) {
      if (seq === searchSeq) errorMsg.value = describe(err)
    } finally {
      if (seq === searchSeq) loading.value = false
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
      emails: [...selectedEmailsLocal.value],
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

  function addEmail(email: string | null) {
    if (!email || selectedEmailsLocal.value.includes(email)) return
    selectedEmailsLocal.value.push(email)
    query.value = ''
    showResults.value = false
    emitModel()
  }

  function removeChip(chip: Chip) {
    if (chip.kind === 'user') {
      selectedUsersLocal.value = selectedUsersLocal.value.filter((u) => u.user_id !== chip.id)
    } else if (chip.kind === 'group') {
      selectedGroupsLocal.value = selectedGroupsLocal.value.filter((g) => g.id !== chip.id)
    } else {
      selectedEmailsLocal.value = selectedEmailsLocal.value.filter((e) => e !== chip.id)
    }
    emitModel()
  }

  function moveCursor(delta: number) {
    const total = optionCount.value
    if (!total) return
    cursorIdx.value = (cursorIdx.value + delta + total) % total
    showResults.value = true
  }

  function selectCursor() {
    const usersLen = filteredUsers.value.length
    const total = optionCount.value
    // Enter with no results (or a stale cursor past the shrunk list) must not
    // deref undefined - bail out instead of crashing the keydown handler.
    if (cursorIdx.value < 0 || cursorIdx.value >= total) return
    if (cursorIdx.value < usersLen) {
      addUser(filteredUsers.value[cursorIdx.value])
    } else if (cursorIdx.value < externalIdx.value) {
      addGroup(filteredGroups.value[cursorIdx.value - usersLen])
    } else {
      addEmail(externalOption.value)
    }
  }

  let blurTimer: ReturnType<typeof setTimeout> | null = null

  function onBlur() {
    // Delay so mousedown on a result row fires first.
    if (blurTimer) clearTimeout(blurTimer)
    blurTimer = setTimeout(() => {
      showResults.value = false
    }, 120)
  }

  onMounted(() => {
    void loadInitialGroups()
    void doSearch('')
  })

  onBeforeUnmount(() => {
    if (searchTimer) clearTimeout(searchTimer)
    if (blurTimer) clearTimeout(blurTimer)
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
