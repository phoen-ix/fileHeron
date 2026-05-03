/* Notifications store — push/markRead/markAll behaviour. The HTTP +
 * SSE plumbing is mocked so we test only the local state transitions
 * the bell relies on. */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useNotificationsStore } from '@/stores/notifications'

vi.mock('@/api/notifications', () => ({
  listNotifications: vi.fn(async () => ({
    data: { items: [], unread_count: 0, page: 1, page_size: 20, total: 0 },
  })),
  markRead: vi.fn(async () => ({ data: { ok: true, unread_count: 0 } })),
  markAllRead: vi.fn(async () => ({ data: { ok: true, unread_count: 0 } })),
}))

beforeEach(() => {
  setActivePinia(createPinia())
})

function fakeNotif(id: number, read = false): any {
  return {
    id,
    category: 'share_created',
    payload: { sender_name: 'Alice', file_count: 1 },
    link_url: '/share/abc',
    created_at: new Date().toISOString(),
    read_at: read ? new Date().toISOString() : null,
  }
}

describe('notifications store', () => {
  it('pushFromSSE prepends and bumps unread count', () => {
    const s = useNotificationsStore()
    s.pushFromSSE(fakeNotif(1))
    s.pushFromSSE(fakeNotif(2))
    expect(s.items.length).toBe(2)
    expect(s.items[0].id).toBe(2)
    expect(s.unreadCount).toBe(2)
  })

  it('pushFromSSE deduplicates by id', () => {
    const s = useNotificationsStore()
    s.pushFromSSE(fakeNotif(1))
    s.pushFromSSE(fakeNotif(1))
    expect(s.items.length).toBe(1)
    expect(s.unreadCount).toBe(2) // we counted the duplicate as new
  })

  it('markRead lowers unread count', async () => {
    const s = useNotificationsStore()
    s.pushFromSSE(fakeNotif(1))
    s.pushFromSSE(fakeNotif(2))
    expect(s.unreadCount).toBe(2)
    await s.markRead(1)
    expect(s.items.find((i) => i.id === 1)?.read_at).not.toBeNull()
    expect(s.unreadCount).toBe(0) // mock returns 0
  })

  it('markRead is a no-op for already-read items', async () => {
    const s = useNotificationsStore()
    s.pushFromSSE(fakeNotif(1, true))
    s.unreadCount = 0
    const beforeRead = s.items[0].read_at
    await s.markRead(1)
    expect(s.items[0].read_at).toBe(beforeRead)
  })

  it('markAllRead clears unread + marks every item', async () => {
    const s = useNotificationsStore()
    s.pushFromSSE(fakeNotif(1))
    s.pushFromSSE(fakeNotif(2))
    s.pushFromSSE(fakeNotif(3))
    await s.markAllRead()
    expect(s.unreadCount).toBe(0)
    expect(s.items.every((i) => i.read_at !== null)).toBe(true)
  })

  it('reset clears state', () => {
    const s = useNotificationsStore()
    s.pushFromSSE(fakeNotif(1))
    s.connected = true
    s.reset()
    expect(s.items).toEqual([])
    expect(s.unreadCount).toBe(0)
    expect(s.connected).toBe(false)
  })
})
