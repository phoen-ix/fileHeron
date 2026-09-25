/* The update-job poll awaited its first tick and then armed a 2 s interval
 * unconditionally: a job already finished on that first look re-armed the poll
 * (a second completion toast), and an unmount landing during the first request
 * left the poll running with nothing to show it. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import en from '@/i18n/locales/en.json'

const getUpdaterJob = vi.fn()
vi.mock('@/api/admin', () => ({
  applyRollback: vi.fn(),
  applyUpdate: vi.fn(),
  cancelPendingUpdate: vi.fn(),
  checkUpdatesNow: vi.fn(),
  forcePendingUpdate: vi.fn(),
  getSystemStatus: vi.fn(() => new Promise(() => {})), // keep the page in its loading state
  getTransferActivity: vi.fn(async () => ({ data: { uploads: 0, downloads: 0, pending_update: null } })),
  getUpdaterJob: (id: string) => getUpdaterJob(id),
  getUpdaterStatus: vi.fn(async () => ({
    data: { current_tag: 'v1.0.0', rollback_target: null, job_in_progress: 'job-1' },
  })),
  runLiveChecks: vi.fn(),
}))
vi.mock('@/api/notifications', () => ({ getStreamToken: vi.fn() }))
vi.mock('@/composables/useSSE', () => ({
  useSSE: () => ({ start: vi.fn(), stop: vi.fn(), connected: ref(false), givenUp: ref(false) }),
}))
const pushToast = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast, confirm: vi.fn() }) }))

import AdminSystem from '@/views/AdminSystem.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSystem, {
    global: {
      plugins: [i18n],
      stubs: {
        RouterLink: true,
        TunableFields: true,
        UpdatesSection: true,
        AdminPageHeader: { template: '<header><slot name="title" /><slot /><slot name="actions" /></header>' },
      },
    },
  })
}

const job = (state: string) => ({
  data: { id: 'job-1', action: 'update', target_tag: 'v1.1.0', state, started_at: '', finished_at: null, log_tail: [], error: null, previous_tag: 'v1.0.0', rollback_reason: null },
})
const jobPolls = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.filter((c) => c[1] === 2000).length

describe('AdminSystem update-job poll', () => {
  let setIntervalSpy: { mock: { calls: unknown[][] }; mockRestore: () => void }
  beforeEach(() => {
    getUpdaterJob.mockReset()
    pushToast.mockReset()
    setIntervalSpy = vi.spyOn(window, 'setInterval')
  })
  afterEach(() => {
    setIntervalSpy.mockRestore()
  })

  it('a job already finished on the first look toasts once and arms no poll', async () => {
    getUpdaterJob.mockResolvedValue(job('healthy'))
    const w = makeWrapper()
    await flushPromises()
    expect(getUpdaterJob).toHaveBeenCalledTimes(1)
    expect(pushToast).toHaveBeenCalledTimes(1)
    expect(jobPolls(setIntervalSpy)).toBe(0)
    w.unmount()
  })

  it('an unmount during the first request leaves no poll behind', async () => {
    let answer!: (v: unknown) => void
    getUpdaterJob.mockImplementation(() => new Promise((res) => (answer = res)))
    const w = makeWrapper()
    await flushPromises()
    w.unmount()
    answer(job('pulling'))
    await flushPromises()
    expect(jobPolls(setIntervalSpy)).toBe(0)
  })

  it('a running job is still polled', async () => {
    getUpdaterJob.mockResolvedValue(job('pulling'))
    const w = makeWrapper()
    await flushPromises()
    expect(jobPolls(setIntervalSpy)).toBe(1)
    w.unmount()
  })
})
