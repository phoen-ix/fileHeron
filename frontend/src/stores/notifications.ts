/* Pinia store for the bell. Holds the most recent N notifications,
 * the unread count, and the SSE connection state. The connection
 * itself is owned by AppHeader which calls start/stop on auth changes. */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  deleteAllNotifications as apiDeleteAll,
  deleteNotification as apiDeleteOne,
  listNotifications,
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
      // The bell is an UNREAD inbox: once a notification is read it drops off
      // on the next load (and the backend hard-deletes it after the retention
      // window). Fetch unread-only so read items never reappear on refresh.
      const { data } = await listNotifications({ unread: true, page_size: RECENT_LIMIT })
      items.value = data.items
      unreadCount.value = data.unread_count
    } finally {
      loading.value = false
    }
  }

  function pushFromSSE(item: NotificationItem) {
    // Drop duplicates (same id) - SSE may deliver an item the list
    // already has during a race with the initial fetch.
    items.value = items.value.filter((i) => i.id !== item.id)
    items.value.unshift(item)
    if (items.value.length > RECENT_LIMIT) items.value.length = RECENT_LIMIT
    if (!item.read_at) unreadCount.value += 1
  }

  async function remove(id: number) {
    const target = items.value.find((i) => i.id === id)
    if (!target) return
    const before = unreadCount.value
    const snapshot = items.value
    // Delete → drop it from the bell immediately (optimistic).
    items.value = items.value.filter((i) => i.id !== id)
    unreadCount.value = Math.max(0, before - 1)
    try {
      const { data } = await apiDeleteOne(id)
      unreadCount.value = data.unread_count
    } catch {
      // Restore truth from the server (the item still exists there).
      items.value = snapshot
      unreadCount.value = before
    }
  }

  async function removeAll() {
    const before = unreadCount.value
    const snapshot = items.value
    items.value = []
    unreadCount.value = 0
    try {
      await apiDeleteAll()
    } catch {
      items.value = snapshot
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
    remove,
    removeAll,
    reset,
  }
})
