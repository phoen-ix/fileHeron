import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const SETTINGS = {
  enabled: false,
  check_mode: 'auto',
  host: 'imap.example.com',
  port: 993,
  user: 'bot',
  is_password_set: true,
  tls_mode: 'implicit',
  mailbox: 'INBOX',
  post_fetch_action: 'mark_read',
  move_folder: 'fileHeron/Processed',
  notify_mode: 'off',
  poll_interval_minutes: 5,
  last_poll_at: null,
  last_success_at: null,
}

const getImapSettings = vi.fn(async () => ({ data: SETTINGS }))
const updateImapSettings = vi.fn(async (p: unknown) => ({ data: { ...SETTINGS, ...(p as object) } }))
const testImap = vi.fn(async () => ({ data: { ok: true, error: null, hint: null, folders: ['INBOX', 'Sent'] } }))
const fetchInboxNow = vi.fn(async () => ({ data: { ok: true, skipped: null, error: null, fetched: 0, ingested: 0 } }))

vi.mock('@/api/admin', () => ({
  getImapSettings: () => getImapSettings(),
  updateImapSettings: (p: unknown) => updateImapSettings(p),
  testImap: () => testImap(),
  fetchInboxNow: () => fetchInboxNow(),
}))

const pushToast = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast }) }))

import AdminSettingsImap from '@/views/AdminSettingsImap.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSettingsImap, { global: { plugins: [i18n] } })
}

describe('AdminSettingsImap', () => {
  beforeEach(() => {
    getImapSettings.mockClear()
    updateImapSettings.mockClear()
    testImap.mockClear()
    pushToast.mockClear()
  })

  it('loads and renders the saved host + password-set hint', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(getImapSettings).toHaveBeenCalled()
    expect((w.find('input[type="text"]').element as HTMLInputElement).value).toBe('imap.example.com')
    expect(w.text()).toContain('A password is saved')
  })

  it('Save keeps the password (sends null) when it was not typed', async () => {
    const w = makeWrapper()
    await flushPromises()
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(updateImapSettings).toHaveBeenCalled()
    expect(updateImapSettings.mock.calls[0][0]).toMatchObject({ password: null })
    expect(pushToast).toHaveBeenCalled()
  })

  it('Test connection shows the folder list', async () => {
    const w = makeWrapper()
    await flushPromises()
    const testBtn = w.findAll('button').find((b) => b.text() === 'Test connection')!
    await testBtn.trigger('click')
    await flushPromises()
    expect(testImap).toHaveBeenCalled()
    expect(w.text()).toContain('Connection OK')
    expect(w.text()).toContain('INBOX, Sent')
  })
})
