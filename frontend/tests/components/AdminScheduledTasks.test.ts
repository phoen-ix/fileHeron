import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const LIST = {
  site_timezone: 'Europe/Vienna',
  items: [
    {
      name: 'expire_files', group: 'shares', description: 'Expire shares.',
      enabled: true, kind: 'interval', interval_minutes: 60, daily_time: '02:00',
      min_interval_minutes: 1, last_run_at: '2026-06-07T10:00:00', last_status: 'success',
      last_duration_ms: 12, last_error: null, next_run_at: '2026-06-07T11:00:00',
      last_24h: { success: 5, failure: 0, running: 0 },
    },
    {
      name: 'prune_history', group: 'maintenance', description: 'Prune history.',
      enabled: true, kind: 'daily', interval_minutes: 1440, daily_time: '02:43',
      min_interval_minutes: 1, last_run_at: null, last_status: null,
      last_duration_ms: null, last_error: null, next_run_at: '2026-06-08T02:43:00',
      last_24h: { success: 0, failure: 0, running: 0 },
    },
  ],
}

const getCrons = vi.fn(async () => ({ data: LIST }))
const updateCronSchedule = vi.fn(async (name: string, p: unknown) => ({
  data: { ...LIST.items[0], name, ...(p as object) },
}))
const runCron = vi.fn(async (_name?: string) => ({ data: { job_name: 'expire_files', queued: true } }))

vi.mock('@/api/admin', () => ({
  getCrons: () => getCrons(),
  updateCronSchedule: (n: string, p: unknown) => updateCronSchedule(n, p),
  runCron: (n: string) => runCron(n),
}))
vi.mock('@/api/notifications', () => ({ getStreamToken: vi.fn(async () => ({ data: { token: 'x' } })) }))
// The page now STARTS the stream (it never did, so a hand-triggered cron showed
// `running` forever - audit #2), so the stub needs the control surface.
vi.mock('@/composables/useSSE', () => ({
  useSSE: () => ({ start: vi.fn(), stop: vi.fn(), connected: { value: false }, givenUp: { value: false } }),
}))

const pushToast = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast }) }))

import AdminScheduledTasks from '@/views/AdminScheduledTasks.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminScheduledTasks, { global: { plugins: [i18n], stubs: { RouterLink: true } } })
}

describe('AdminScheduledTasks', () => {
  beforeEach(() => {
    getCrons.mockClear()
    updateCronSchedule.mockClear()
    runCron.mockClear()
    pushToast.mockClear()
  })

  it('loads and groups the crons', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(getCrons).toHaveBeenCalled()
    expect(w.text()).toContain('expire_files')
    expect(w.text()).toContain('prune_history')
    expect(w.text()).toContain('Shares & files')
    expect(w.text()).toContain('Maintenance')
    // interval row shows a number input; daily row a time input.
    expect(w.find('input[type="number"]').exists()).toBe(true)
    expect(w.find('input[type="time"]').exists()).toBe(true)
  })

  it('Save persists the row schedule', async () => {
    const w = makeWrapper()
    await flushPromises()
    await w.find('input[type="number"]').setValue(30)
    await w.findAll('button').find((b) => b.text() === 'Save')!.trigger('click')
    await flushPromises()
    expect(updateCronSchedule).toHaveBeenCalledWith(
      'expire_files',
      expect.objectContaining({ interval_minutes: 30, kind: 'interval' }),
    )
    expect(pushToast).toHaveBeenCalled()
  })

  it('Run now triggers the cron', async () => {
    const w = makeWrapper()
    await flushPromises()
    await w.findAll('button').find((b) => b.text() === 'Run now')!.trigger('click')
    await flushPromises()
    expect(runCron).toHaveBeenCalledWith('expire_files')
  })
})
