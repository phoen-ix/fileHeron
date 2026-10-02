/* The user/group allowlist half of a "who may" policy: a debounced user search,
 * adding and removing users, toggling groups. Every policy page carried its own
 * copy of this (API tokens, public links, share approval, both Secrets gates);
 * components/admin/PolicyGate.vue and SecretPolicySide.vue now share it. */
import { onBeforeUnmount, ref, watch } from 'vue'

import { searchUsers } from '@/api/users'
import type { UserSearchItem } from '@/types/api'

export interface PolicyGateUser {
  id: number
  display_name: string
  email: string
  role: string
}

export interface PolicyGateGroup {
  id: number
  name: string
}

export interface PolicyGateValue<M extends string> {
  mode: M
  users: PolicyGateUser[]
  groups: PolicyGateGroup[]
}

export function usePolicyAllowlist(opts: {
  users: () => PolicyGateUser[]
  setUsers: (users: PolicyGateUser[]) => void
  /** Search results never offered, beyond those already on the list. */
  exclude?: (u: UserSearchItem) => boolean
}) {
  const query = ref('')
  const suggestions = ref<UserSearchItem[]>([])
  let timer: ReturnType<typeof setTimeout> | null = null

  watch(query, (q) => {
    if (timer) clearTimeout(timer)
    if (!q || q.length < 2) {
      suggestions.value = []
      return
    }
    timer = setTimeout(async () => {
      try {
        const { data } = await searchUsers(q)
        suggestions.value = data.items.filter(
          (u) => !opts.users().some((x) => x.id === u.user_id) && !opts.exclude?.(u),
        )
      } catch {
        suggestions.value = []
      }
    }, 200)
  })
  onBeforeUnmount(() => {
    if (timer) clearTimeout(timer)
  })

  function addUser(u: UserSearchItem) {
    opts.setUsers([
      ...opts.users(),
      { id: u.user_id, display_name: u.display_name, email: u.email, role: u.role },
    ])
    query.value = ''
    suggestions.value = []
  }

  function removeUser(id: number) {
    opts.setUsers(opts.users().filter((u) => u.id !== id))
  }

  return { query, suggestions, addUser, removeUser }
}

/** `groups` with `g` added, or removed if it was there. */
export function toggledGroups(groups: PolicyGateGroup[], g: PolicyGateGroup): PolicyGateGroup[] {
  return groups.some((x) => x.id === g.id)
    ? groups.filter((x) => x.id !== g.id)
    : [...groups, { id: g.id, name: g.name }]
}
