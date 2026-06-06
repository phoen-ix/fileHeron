import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const DETAIL = {
  id: 1,
  created_at: '2026-06-01T10:00:00',
  received_at: '2026-06-01T10:00:00',
  sender_email: 'grace@example.com',
  sender_name: 'Grace',
  sender_user_id: null,
  subject: 'Re: your files',
  classification: 'normal',
  status: 'new',
  has_attachments: true,
  to_addr: 'noreply@fileheron.local',
  message_id: '<x@y>',
  in_reply_to: null,
  body_text: 'plain body',
  body_html: '<p>hello <b>world</b></p>',
  attachments: [
    { id: 9, filename: 'a.pdf', content_type: 'application/pdf', size_bytes: 1024, av_state: 'clean' },
  ],
}

const getInboxMessage = vi.fn(async () => ({ data: DETAIL }))
const updateInboxStatus = vi.fn(async () => ({ data: { ...DETAIL, status: 'read' } }))
vi.mock('@/api/admin', () => ({
  getInboxMessage: () => getInboxMessage(),
  updateInboxStatus: () => updateInboxStatus(),
  deleteInboxMessage: vi.fn(),
  downloadInboxAttachment: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '1' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

const pushToast = vi.fn()
const confirm = vi.fn(async () => true)
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast, confirm }) }))

import AdminInboxDetail from '@/views/AdminInboxDetail.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminInboxDetail, {
    global: { plugins: [i18n], stubs: { RouterLink: true } },
  })
}

describe('AdminInboxDetail', () => {
  beforeEach(() => {
    getInboxMessage.mockClear()
    updateInboxStatus.mockClear()
  })

  it('renders the message and its HTML body in a sandboxed iframe', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(getInboxMessage).toHaveBeenCalled()
    expect(w.text()).toContain('Re: your files')
    const frame = w.find('iframe.body-frame')
    expect(frame.exists()).toBe(true)
    expect(frame.attributes('sandbox')).toBe('')
    expect(frame.attributes('srcdoc')).toContain('<b>world</b>')
  })

  it('auto-marks a new message as read on open', async () => {
    makeWrapper()
    await flushPromises()
    expect(updateInboxStatus).toHaveBeenCalled()
  })

  it('shows a clean attachment with a download action', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(w.text()).toContain('a.pdf')
    expect(w.findAll('button').some((b) => b.text() === 'Download')).toBe(true)
  })
})
