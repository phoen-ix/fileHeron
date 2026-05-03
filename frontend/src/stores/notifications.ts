/* Pinia store for the bell. Holds the most recent N notifications,
 * the unread count, and the SSE connection state. The connection
 * itself is owned by AppHeader which calls start/stop on auth changes. */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  listNotifications,
  markAllRead as apiMarkAllRead,
  markRead as apiMarkRead,
} from '@/api/notifications'
import type { NotificationItem } from '@/types/api'

const RECENT_LIMIT = 20

export const useNotificationsStore = defineStore('notifications', () => {
  const items = ref<NotificationItem[]>([])
  const unreadCount = ref(0)
  const connected = ref(false)
  const loading = ref(false)

  const hasUnread = computed(() => unreadCount.value > 0)

  async function refresh() {
    loading.value = true
    try {
      const { data } = await listNotifications({ page_size: RECENT_LIMIT })
      items.value = data.items
      unreadCount.value = data.unread_count
    } finally {
      loading.value = false
    }
  }

  function pushFromSSE(item: NotificationItem) {
    // Drop duplicates (same id) — SSE may deliver an item the list
    // already has during a race with the initial fetch.
    items.value = items.value.filter((i) => i.id !== item.id)
    items.value.unshift(item)
    if (items.value.length > RECENT_LIMIT) items.value.length = RECENT_LIMIT
    if (!item.read_at) unreadCount.value += 1
  }

  async function markRead(id: number) {
    const target = items.value.find((i) => i.id === id)
    if (!target || target.read_at) return
    const before = unreadCount.value
    target.read_at = new Date().toISOString()
    unreadCount.value = Math.max(0, before - 1)
    try {
      const { data } = await apiMarkRead(id)
      unreadCount.value = data.unread_count
    } catch {
      // Roll back on failure.
      target.read_at = null
      unreadCount.value = before
    }
  }

  async function markAllRead() {
    const before = unreadCount.value
    const now = new Date().toISOString()
    items.value.forEach((i) => {
      if (!i.read_at) i.read_at = now
    })
    unreadCount.value = 0
    try {
      await apiMarkAllRead()
    } catch {
      // Rollback is annoying — refetch instead.
      void refresh()
      unreadCount.value = before
    }
  }

  function reset() {
    items.value = []
    unreadCount.value = 0
    connected.value = false
  }

  return {
    items,
    unreadCount,
    connected,
    loading,
    hasUnread,
    refresh,
    pushFromSSE,
    markRead,
    markAllRead,
    reset,
  }
})
