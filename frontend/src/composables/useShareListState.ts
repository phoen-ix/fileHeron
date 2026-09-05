import { computed, onScopeDispose, ref, watch } from 'vue'
import type { ComputedRef } from 'vue'
import { useI18n } from 'vue-i18n'

import { listGroups } from '@/api/groups'
import { listShares } from '@/api/shares'
import { searchUsers } from '@/api/users'
import { useApiError } from '@/composables/useApiError'
import { useTableSort } from '@/composables/useTableSort'
import type {
  GroupResponse,
  ShareListItem,
  ShareState,
  UserSearchItem,
} from '@/types/api'

export type GroupBy =
  | 'none'
  | 'recipient_user'
  | 'recipient_group'
  | 'sender'
  | 'via_group'

interface RenderGroup {
  key: string
  label: string
  items: ShareListItem[]
}

export function useShareListState(box: ComputedRef<'outbox' | 'inbox'>) {
  const { t } = useI18n()
  const { describe } = useApiError()

  const items = ref<ShareListItem[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(50)
  const loading = ref(true)
  const errorMsg = ref<string | null>(null)

  // Outbox-only bulk selection. Keeps it co-located with the rest of
  // the list state so the view stays a thin shell.
  const selectedIds = ref<Set<string>>(new Set())
  const selectedCount = computed(() => selectedIds.value.size)
  function isSelected(id: string): boolean {
    return selectedIds.value.has(id)
  }
  function toggleSelected(id: string) {
    const next = new Set(selectedIds.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    selectedIds.value = next
  }
  function clearSelection() {
    selectedIds.value = new Set()
  }
  function selectAllActive() {
    const next = new Set<string>()
    for (const it of items.value) {
      if (it.state === 'active') next.add(it.id)
    }
    selectedIds.value = next
  }

  /** Select (or clear) exactly the active shares in ONE group.
   *
   * A grouped list gives every group header its own checkbox, and it used to
   * call `selectAllActive` - so ticking "the three shares to this contractor"
   * selected all forty active shares on the page, and the action behind the
   * bulk bar unlinks bytes from disk (audit #2). */
  function setGroupSelection(ids: string[], selected: boolean) {
    const next = new Set(selectedIds.value)
    for (const id of ids) {
      if (selected) next.add(id)
      else next.delete(id)
    }
    selectedIds.value = next
  }

  // Default to 'active' so the list opens with only usable shares; the
  // dropdown still has "All states" for opt-in.
  const stateFilter = ref<ShareState | ''>('active')
  const partyKind = ref<'any' | 'user' | 'group'>('any')
  const partyUser = ref<UserSearchItem | null>(null)
  const partyGroup = ref<GroupResponse | null>(null)
  const userQuery = ref('')
  const userSuggestions = ref<UserSearchItem[]>([])
  const myGroups = ref<GroupResponse[]>([])

  // Free-text subject search. The backend already filters
  // Share.subject ILIKE %q% (services/share.py); this just feeds it.
  const subjectQuery = ref('')

  const groupBy = ref<GroupBy>('none')
  const sort = useTableSort({ defaultBy: 'created_at', defaultDir: 'desc' })

  let userSearchTimer: ReturnType<typeof setTimeout> | null = null
  watch(userQuery, () => {
    if (userSearchTimer) clearTimeout(userSearchTimer)
    if (!userQuery.value || userQuery.value.length < 2) {
      userSuggestions.value = []
      return
    }
    userSearchTimer = setTimeout(async () => {
      try {
        const { data } = await searchUsers(userQuery.value)
        userSuggestions.value = data.items
      } catch {
        userSuggestions.value = []
      }
    }, 200)
  })

  let subjectSearchTimer: ReturnType<typeof setTimeout> | null = null
  watch(subjectQuery, () => {
    if (subjectSearchTimer) clearTimeout(subjectSearchTimer)
    subjectSearchTimer = setTimeout(() => {
      page.value = 1
      void load()
    }, 250)
  })

  // A keystroke followed by leaving the page must not fire a search or a
  // reload against a list that no longer exists.
  onScopeDispose(() => {
    if (userSearchTimer) clearTimeout(userSearchTimer)
    if (subjectSearchTimer) clearTimeout(subjectSearchTimer)
  })

  function pickUser(u: UserSearchItem) {
    partyUser.value = u
    partyGroup.value = null
    userQuery.value = u.display_name
    userSuggestions.value = []
    page.value = 1
    void load()
  }

  function clearParty() {
    partyKind.value = 'any'
    partyUser.value = null
    partyGroup.value = null
    userQuery.value = ''
    userSuggestions.value = []
    page.value = 1
    void load()
  }

  function clearAllFilters() {
    stateFilter.value = 'active'
    subjectQuery.value = ''
    clearParty()
  }

  function pickGroup() {
    page.value = 1
    void load()
  }

  watch(partyKind, async () => {
    partyUser.value = null
    partyGroup.value = null
    userQuery.value = ''
    if (partyKind.value === 'group' && myGroups.value.length === 0) {
      try {
        const { data } = await listGroups()
        myGroups.value = data.items
      } catch {
        /* leave empty */
      }
    }
    page.value = 1
    void load()
  })

  watch(stateFilter, () => {
    page.value = 1
    void load()
  })

  watch([sort.sortBy, sort.sortDir], () => {
    void load()
  })

  watch(page, () => {
    void load()
  })

  let seq = 0

  async function load() {
    const mine = ++seq
    loading.value = true
    errorMsg.value = null
    try {
      const params: Parameters<typeof listShares>[0] = {
        box: box.value,
        page: page.value,
        page_size: pageSize.value,
        sort: sort.sortBy.value,
        direction: sort.sortDir.value,
      }
      if (stateFilter.value) params.state = [stateFilter.value]
      if (subjectQuery.value.trim()) params.q = subjectQuery.value.trim()

      if (box.value === 'outbox') {
        if (partyKind.value === 'user' && partyUser.value) {
          params.recipient_user_id = partyUser.value.user_id
        } else if (partyKind.value === 'group' && partyGroup.value) {
          params.recipient_group_id = partyGroup.value.id
        }
      } else {
        if (partyKind.value === 'user' && partyUser.value) {
          params.sender_user_id = partyUser.value.user_id
        } else if (partyKind.value === 'group' && partyGroup.value) {
          params.via_group_id = partyGroup.value.id
        }
      }

      const { data } = await listShares(params)
      if (mine !== seq) return // superseded by a newer load()
      // Bulk-expiring the last rows of the last page (or a filter that empties
      // it) can leave us on an out-of-range page with total > 0 and no Pager to
      // get back. Clamp to the last valid page and reload instead of stranding.
      const maxPage = Math.max(1, Math.ceil(data.total / pageSize.value))
      if (data.total > 0 && page.value > maxPage) {
        page.value = maxPage // triggers watch(page) -> reload
        return
      }
      items.value = data.items
      total.value = data.total
    } catch (err) {
      if (mine !== seq) return
      errorMsg.value = describe(err)
    } finally {
      if (mine === seq) loading.value = false
    }
  }

  watch(box, () => {
    page.value = 1
    clearParty()
    groupBy.value = 'none'
    stateFilter.value = 'active'
    subjectQuery.value = ''
    sort.reset()
    clearSelection()
  })

  watch([page, stateFilter, partyKind, partyUser, partyGroup], () => {
    // Selection IDs from a different page / filter would survive into
    // the new view as ghosts. Reset on any boundary change.
    clearSelection()
  })

  // ---- group rendering -----------------------------------------------------

  const groupedItems = computed<RenderGroup[]>(() => {
    if (groupBy.value === 'none') {
      return [{ key: 'all', label: '', items: items.value }]
    }
    const map = new Map<string, RenderGroup>()
    for (const item of items.value) {
      const keys = collectGroupKeys(item)
      for (const k of keys) {
        const existing = map.get(k.key)
        if (existing) {
          existing.items.push(item)
        } else {
          map.set(k.key, { key: k.key, label: k.label, items: [item] })
        }
      }
    }
    return [...map.values()].sort((a, b) => a.label.localeCompare(b.label))
  })

  function collectGroupKeys(
    item: ShareListItem,
  ): { key: string; label: string }[] {
    if (groupBy.value === 'sender' && item.sender) {
      return [{ key: `u-${item.sender.id}`, label: item.sender.display_name }]
    }
    if (groupBy.value === 'recipient_user') {
      const out: { key: string; label: string }[] = []
      for (const r of item.recipients) {
        if (r.kind === 'user') out.push({ key: `u-${r.id}`, label: r.label })
      }
      if (out.length === 0) {
        out.push({ key: 'no-user', label: t('share_list.group.no_user') })
      }
      return out
    }
    if (
      groupBy.value === 'recipient_group' ||
      groupBy.value === 'via_group'
    ) {
      const out: { key: string; label: string }[] = []
      for (const r of item.recipients) {
        if (r.kind === 'group') out.push({ key: `g-${r.id}`, label: r.label })
      }
      if (out.length === 0) {
        out.push({ key: 'no-group', label: t('share_list.group.no_group') })
      }
      return out
    }
    return [{ key: 'all', label: '' }]
  }

  const groupByOptions = computed<{ value: GroupBy; label: string }[]>(() => {
    if (box.value === 'outbox') {
      return [
        { value: 'none', label: t('share_list.group.none') },
        { value: 'recipient_user', label: t('share_list.group.recipient_user') },
        { value: 'recipient_group', label: t('share_list.group.recipient_group') },
      ]
    }
    return [
      { value: 'none', label: t('share_list.group.none') },
      { value: 'sender', label: t('share_list.group.sender') },
      { value: 'via_group', label: t('share_list.group.via_group') },
    ]
  })

  return {
    // state
    items,
    total,
    page,
    pageSize,
    loading,
    errorMsg,
    stateFilter,
    partyKind,
    partyUser,
    partyGroup,
    userQuery,
    userSuggestions,
    myGroups,
    subjectQuery,
    groupBy,
    sort,
    selectedIds,
    selectedCount,
    // computed
    groupedItems,
    groupByOptions,
    // methods
    pickUser,
    clearParty,
    clearAllFilters,
    pickGroup,
    load,
    isSelected,
    toggleSelected,
    clearSelection,
    selectAllActive,
    setGroupSelection,
  }
}
