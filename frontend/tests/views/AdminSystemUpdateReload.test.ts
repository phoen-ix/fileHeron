/* The tab that ran an in-app update reloads into the new bundle once the job is
 * healthy - it still runs the old one, whose next lazy page would 404 on a chunk
 * the update replaced - and the reloaded page says so. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  status: vi.fn(),
  updater: vi.fn(),
  job: vi.fn(),
  activity: vi.fn(),
}))
vi.mock('@/api/admin', () => ({
  applyRollback: vi.fn(),
  applyUpdate: vi.fn(),
  cancelPendingUpdate: vi.fn(),
  checkUpdatesNow: vi.fn(),
  forcePendingUpdate: vi.fn(),
  runLiveChecks: vi.fn(),
  getSystemStatus: () => api.status(),
  getTransferActivity: () => api.activity(),
  getUpdaterJob: (id: string) => api.job(id),
  getUpdaterStatus: () => api.updater(),
}))
vi.mock('@/api/notifications', () => ({ getStreamToken: vi.fn() }))
vi.mock('@/composables/useSSE', () => ({
  useSSE: () => ({ start: vi.fn(), stop: vi.fn(), connected: ref(false) }),
}))
const stale = vi.hoisted(() => ({ reload: vi.fn(), take: vi.fn() }))
vi.mock('@/utils/staleBundle', () => ({
  reloadAfterUpdate: (jobId: string, tag: string) => stale.reload(jobId, tag),
  takeUpdateDoneNotice: () => stale.take(),
}))

import { useUiStore } from '@/stores/ui'
import AdminSystem from '@/views/AdminSystem.vue'

function job(state: string) {
  return {
    id: 'job-1',
    action: 'update',
    target_tag: 'v9.9.9',
    previous_tag: 'v9.9.8',
    state,
    log_tail: [],
    error: null,
  }
}

function mountPage() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSystem, {
    global: {
      plugins: [i18n],
      stubs: {
        AdminPageHeader: { template: '<header><slot /></header>' },
        AutoUpdateSection: true,
        TunableFields: true,
        UpdatesSection: true,
        RouterLink: { template: '<a><slot /></a>' },
      },
    },
  })
}

describe('AdminSystem after an update', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    api.status.mockReturnValue(new Promise(() => {})) // the page body is not under test
    api.activity.mockResolvedValue({ data: { uploads: 0, downloads: 0, pending_update: null } })
    api.updater.mockResolvedValue({ data: { job_in_progress: 'job-1' } })
    stale.take.mockReturnValue(null)
    stale.reload.mockResolvedValue(true)
  })

  it('reloads into the new bundle when the job turns healthy', async () => {
    api.job.mockResolvedValue({ data: job('healthy') })
    const w = mountPage()
    await flushPromises()
    expect(stale.reload).toHaveBeenCalledWith('job-1', 'v9.9.9')
    w.unmount()
  })

  it('does not reload for a job that failed or rolled back', async () => {
    for (const state of ['failed', 'rolled_back']) {
      api.job.mockResolvedValue({ data: job(state) })
      const w = mountPage()
      await flushPromises()
      w.unmount()
    }
    expect(stale.reload).not.toHaveBeenCalled()
  })

  it('says which version the reloaded page now runs', async () => {
    api.updater.mockResolvedValue({ data: { job_in_progress: null } })
    stale.take.mockReturnValue('v9.9.9')
    const w = mountPage()
    await flushPromises()
    const texts = useUiStore().toasts.map((x) => x.text)
    expect(texts).toContain('Page reloaded - you are now using v9.9.9.')
    expect(stale.reload).not.toHaveBeenCalled()
    w.unmount()
  })
})
