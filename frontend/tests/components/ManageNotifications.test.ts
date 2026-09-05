import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const ITEMS = [
  { category: 'share_created', channel: 'both', locked: false, one_click: true },
  { category: 'login_alert', channel: 'email', locked: true, one_click: false },
  // Operational alert: changeable here, but never by following a link from an
  // email - `one_click` is what the view keys on, separately from `locked`,
  // because this row is NOT read-only.
  { category: 'ops_alert', channel: 'both', locked: false, one_click: false },
]

const fetchSubscriptions = vi.fn(async (_t: string) => ({
  data: { display_name: 'Dana', items: ITEMS.map((i) => ({ ...i })) },
}))
const updateSubscriptions = vi.fn(async (_t: string, _p: Record<string, string>) => ({
  data: { display_name: 'Dana', items: ITEMS.map((i) => ({ ...i })) },
}))
const unsubscribeCategory = vi.fn(async (_t: string, category: string) => ({
  data: {
    items: ITEMS.map((i) => (i.category === category ? { ...i, channel: 'off' } : { ...i })),
    category,
    previous_channel: 'both',
  },
}))

vi.mock('@/api/notificationSubscriptions', () => ({
  fetchSubscriptions: (t: string) => fetchSubscriptions(t),
  updateSubscriptions: (t: string, p: Record<string, string>) => updateSubscriptions(t, p),
  unsubscribeCategory: (t: string, c: string) => unsubscribeCategory(t, c),
}))

let routeQuery: Record<string, string> = {}
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { token: 'tok123' }, query: routeQuery }),
}))

import ManageNotifications from '@/views/ManageNotifications.vue'

function makeWrapper() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(ManageNotifications, { global: { plugins: [i18n] } })
}

describe('ManageNotifications', () => {
  beforeEach(() => {
    routeQuery = {}
    fetchSubscriptions.mockClear()
    updateSubscriptions.mockClear()
    unsubscribeCategory.mockClear()
  })

  it('loads subscriptions by token and shows the name', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(fetchSubscriptions).toHaveBeenCalledWith('tok123')
    expect(w.text()).toContain('Dana')
    expect(w.text()).toContain('Share')
  })

  it('renders a locked row with a disabled select + required note', async () => {
    const w = makeWrapper()
    await flushPromises()
    const selects = w.findAll('select')
    // login_alert is the locked row -> its select is disabled.
    const disabled = selects.filter((s) => (s.element as HTMLSelectElement).disabled)
    expect(disabled.length).toBe(1)
    expect(w.text()).toContain('Required')
  })

  it('?off auto-applies the unsubscribe and offers Undo', async () => {
    routeQuery = { off: 'share_created' }
    const w = makeWrapper()
    await flushPromises()
    expect(unsubscribeCategory).toHaveBeenCalledWith('tok123', 'share_created')
    expect(w.text()).toContain('unsubscribed')
    // Undo restores the previous channel via an update call.
    await w.findAll('button').find((b) => b.text() === 'Undo')!.trigger('click')
    await flushPromises()
    expect(updateSubscriptions).toHaveBeenCalledWith('tok123', { share_created: 'both' })
  })

  it('?off still works when the backend does not send one_click', async () => {
    // A rolling update, or a cached bundle against an older backend. Reading
    // the absent flag as "not allowed" would silently break unsubscribe for
    // every category; the server is the authority and refuses what it must.
    fetchSubscriptions.mockResolvedValueOnce({
      data: {
        display_name: 'Dana',
        items: [{ category: 'share_created', channel: 'both', locked: false }],
      },
    } as never)
    routeQuery = { off: 'share_created' }
    const w = makeWrapper()
    await flushPromises()
    expect(unsubscribeCategory).toHaveBeenCalledWith('tok123', 'share_created')
    expect(w.text()).toContain('unsubscribed')
  })

  it('?off does NOT auto-apply for an operational alert', async () => {
    // ops_alert / server_error are the instance reporting that it is broken.
    // Mail sent before that rule existed still carries `?off=ops_alert`, so
    // landing on this page must show the preferences rather than silently
    // ending the alerting. The row is not `locked` - it stays changeable here.
    routeQuery = { off: 'ops_alert' }
    const w = makeWrapper()
    await flushPromises()
    expect(unsubscribeCategory).not.toHaveBeenCalled()
    expect(w.text()).not.toContain('unsubscribed')
  })

  it('changing a channel saves it', async () => {
    const w = makeWrapper()
    await flushPromises()
    const select = w.findAll('select')[0]
    await select.setValue('off')
    await flushPromises()
    expect(updateSubscriptions).toHaveBeenCalledWith('tok123', { share_created: 'off' })
  })

  it('a refused save puts the row back and says so', async () => {
    // The browser has already moved the dropdown when the request fails. This
    // page had no catch at all: an unhandled rejection, a select showing a
    // preference the server does not have, and no message - the defect the
    // signed-in preferences table fixed (fe-correct-5), missed here.
    updateSubscriptions.mockRejectedValueOnce({ response: { status: 500 } })
    const w = makeWrapper()
    await flushPromises()
    const select = w.findAll('select')[0]
    await select.setValue('off')
    await flushPromises()

    expect((select.element as HTMLSelectElement).value).toBe('both')
    expect(w.text()).toContain('could not be saved')
    expect((select.element as HTMLSelectElement).disabled).toBe(false)
  })

  it('shows the invalid-link state on a bad token', async () => {
    fetchSubscriptions.mockRejectedValueOnce({ response: { data: { code: 'MANAGE_TOKEN_EXPIRED' } } })
    const w = makeWrapper()
    await flushPromises()
    expect(w.text()).toContain('no longer valid')
    expect(w.text()).toContain('expired')
  })
})
