/* Notifications store - push / remove / removeAll behaviour. The HTTP + SSE
 * plumbing is mocked so we test only the local state transitions the bell
 * relies on. The bell is a delete-to-dismiss inbox (no read/unread). */
import { setActivePinia, createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useNotificationsStore } from '@/stores/notifications'

vi.mock('@/api/notifications', () => ({
  listNotifications: vi.fn(async () => ({
    data: { items: [], unread_count: 0, page: 1, page_size: 20, total: 0 },
  })),
  deleteNotification: vi.fn(async () => ({ data: { ok: true, unread_count: 0 } })),
  deleteAllNotifications: vi.fn(async () => ({ data: { ok: true, unread_count: 0 } })),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  // The api mocks are module-level vi.fn()s; without clearing, a delete call
  // from one test leaks into the next test's call-count assertions.
  vi.clearAllMocks()
})

function fakeNotif(id: number): any {
  return {
    id,
    category: 'share_created',
    payload: { sender_name: 'Alice', file_count: 1 },
    link_url: '/share/abc',
    created_at: new Date().toISOString(),
    read_at: null,
  }
}

describe('notifications store', () => {
  it('pushFromSSE prepends and bumps the count', () => {
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
    expect(s.unreadCount).toBe(2) // counted the duplicate as new
  })

  it('remove deletes the item from the bell + lowers the count', async () => {
    const s = useNotificationsStore()
    s.pushFromSSE(fakeNotif(1))
    s.pushFromSSE(fakeNotif(2))
    expect(s.unreadCount).toBe(2)
    await s.remove(1)
    expect(s.items.find((i) => i.id === 1)).toBeUndefined()
    expect(s.items.length).toBe(1)
    expect(s.unreadCount).toBe(0) // mock returns 0
  })

  it('remove is a no-op for an unknown id', async () => {
    const api: any = await import('@/api/notifications')
    const s = useNotificationsStore()
    s.pushFromSSE(fakeNotif(1))
    await s.remove(999)
    expect(s.items.length).toBe(1)
    expect(api.deleteNotification).not.toHaveBeenCalled()
  })

  it('refresh fetches the inbox', async () => {
    const api: any = await import('@/api/notifications')
    const s = useNotificationsStore()
    await s.refresh()
    expect(api.listNotifications).toHaveBeenCalledWith(
      expect.objectContaining({ unread: true }),
    )
  })

  it('removeAll empties the bell', async () => {
    const s = useNotificationsStore()
    s.pushFromSSE(fakeNotif(1))
    s.pushFromSSE(fakeNotif(2))
    s.pushFromSSE(fakeNotif(3))
    await s.removeAll()
    expect(s.unreadCount).toBe(0)
    expect(s.items).toEqual([])
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
