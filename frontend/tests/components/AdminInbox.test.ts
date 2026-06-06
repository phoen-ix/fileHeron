import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const LIST = {
  items: [
    { id: 1, created_at: '2026-06-01T10:00:00', received_at: null, sender_email: 'grace@example.com', sender_name: 'Grace', sender_user_id: null, subject: 'Re: files', classification: 'normal', status: 'new', has_attachments: false },
    { id: 2, created_at: '2026-06-01T09:00:00', received_at: null, sender_email: 'MAILER-DAEMON@mx', sender_name: null, sender_user_id: null, subject: 'Delivery failed', classification: 'bounce', status: 'read', has_attachments: false },
  ],
  total: 2,
  page: 1,
  page_size: 50,
  unread: 1,
}

const listInbox = vi.fn(async (_params?: unknown) => ({ data: LIST }))
const fetchInboxNow = vi.fn(async () => ({
  data: { ok: true, skipped: null, error: null, fetched: 0, ingested: 0, mailbox: 'INBOX', total: 0 },
}))
vi.mock('@/api/admin', () => ({
  listInbox: (p: unknown) => listInbox(p),
  fetchInboxNow: () => fetchInboxNow(),
}))

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

const pushToast = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast }) }))

import AdminInbox from '@/views/AdminInbox.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminInbox, { global: { plugins: [i18n], stubs: { Pager: true } } })
}

describe('AdminInbox', () => {
  beforeEach(() => {
    listInbox.mockClear()
    push.mockClear()
    fetchInboxNow.mockClear()
    pushToast.mockClear()
  })

  it('Fetch now triggers a fetch, reloads, and explains an empty result', async () => {
    const w = makeWrapper()
    await flushPromises()
    listInbox.mockClear()
    await w.findAll('button').find((b) => b.text() === 'Fetch now')!.trigger('click')
    await flushPromises()
    expect(fetchInboxNow).toHaveBeenCalled()
    expect(listInbox).toHaveBeenCalled() // reloaded
    expect(pushToast).toHaveBeenCalled()
  })

  it('lists messages with classification badges', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(listInbox).toHaveBeenCalled()
    expect(w.text()).toContain('Re: files')
    expect(w.text()).toContain('BOUNCE')
    expect(w.text()).toContain('REPLY')
  })

  it('clicking a row navigates to the detail view', async () => {
    const w = makeWrapper()
    await flushPromises()
    await w.find('tbody tr').trigger('click')
    expect(push).toHaveBeenCalledWith({ name: 'admin-inbox-detail', params: { id: 1 } })
  })

  it('changing the classification filter refetches', async () => {
    const w = makeWrapper()
    await flushPromises()
    listInbox.mockClear()
    const select = w.findAll('select')[0]
    await select.setValue('bounce')
    await flushPromises()
    expect(listInbox).toHaveBeenCalledWith(expect.objectContaining({ classification: 'bounce' }))
  })
})
