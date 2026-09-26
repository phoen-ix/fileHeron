/* The Update dialog's "Back up database first" box: it starts from the admin
 * setting (or the choice stored with a postponed update), its value reaches the
 * API on both update paths, rollback does not offer it, and the job banner shows
 * the executor's phase, backup directory and warnings. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import en from '@/i18n/locales/en.json'

const applyUpdate = vi.fn()
const forcePendingUpdate = vi.fn()
const applyRollback = vi.fn()
const getUpdaterStatus = vi.fn()
const getUpdaterJob = vi.fn()
const getTransferActivity = vi.fn()

const systemStatus = {
  live: {
    checked_at: null,
    db: { status: 'ok', error: null },
    redis: { status: 'ok', error: null },
    av: { status: 'ok', error: null },
  },
  crons: [],
  recent_failures: [],
  email_undeliverable_24h: 0,
  version: {
    running: 'v1.0.0',
    sha: 'abc',
    running_release_url: null,
    latest: 'v1.1.0',
    update_available: true,
    last_check_at: null,
    last_success_at: null,
    last_check_error: null,
    release_notes: null,
    release_url: null,
    release_published_at: null,
  },
}

vi.mock('@/api/admin', () => ({
  applyRollback: (...a: unknown[]) => applyRollback(...a),
  applyUpdate: (...a: unknown[]) => applyUpdate(...a),
  cancelPendingUpdate: vi.fn(),
  checkUpdatesNow: vi.fn(),
  forcePendingUpdate: (...a: unknown[]) => forcePendingUpdate(...a),
  getSystemStatus: vi.fn(async () => ({ data: systemStatus })),
  getTransferActivity: () => getTransferActivity(),
  getUpdaterJob: (id: string) => getUpdaterJob(id),
  getUpdaterStatus: () => getUpdaterStatus(),
  runLiveChecks: vi.fn(),
}))
vi.mock('@/api/notifications', () => ({ getStreamToken: vi.fn() }))
vi.mock('@/composables/useSSE', () => ({
  useSSE: () => ({ start: vi.fn(), stop: vi.fn(), connected: ref(false), givenUp: ref(false) }),
}))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast: vi.fn(), confirm: vi.fn() }) }))

import AdminSystem from '@/views/AdminSystem.vue'

function updaterStatus(over: Record<string, unknown> = {}) {
  return {
    data: {
      current_tag: 'v1.0.0',
      rollback_target: 'v0.9.0',
      rollback_alembic_head_known: true,
      job_in_progress: null,
      backup_default: true,
      backup_on_db_change: true,
      ...over,
    },
  }
}

function activity(pending: Record<string, unknown> | null = null) {
  return {
    data: {
      active_uploads: 0,
      active_downloads: 0,
      maintenance_enabled: !!pending,
      pending_update: pending,
    },
  }
}

async function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(AdminSystem, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: {
        RouterLink: true,
        TunableFields: true,
        UpdatesSection: true,
        AdminPageHeader: {
          template: '<header><slot name="title" /><slot /><slot name="actions" /></header>',
        },
      },
    },
  })
  await flushPromises()
  return w
}

async function openDialog(w: Awaited<ReturnType<typeof makeWrapper>>, label: string) {
  const btn = w.findAll('button').find((b) => b.text().includes(label))
  expect(btn, `no button labelled ${label}`).toBeTruthy()
  await btn!.trigger('click')
  await flushPromises()
}

async function submit(w: Awaited<ReturnType<typeof makeWrapper>>) {
  await w.find('.fh-modal input[type="password"]').setValue('pw')
  await w.find('.fh-modal form').trigger('submit')
  await flushPromises()
}

const backupBox = (w: Awaited<ReturnType<typeof makeWrapper>>) =>
  w.find<HTMLInputElement>('[data-testid="update-backup"]')

describe('AdminSystem update dialog - pre-update backup', () => {
  beforeEach(() => {
    for (const f of [
      applyUpdate,
      forcePendingUpdate,
      applyRollback,
      getUpdaterStatus,
      getUpdaterJob,
      getTransferActivity,
    ]) {
      f.mockReset()
    }
    getTransferActivity.mockResolvedValue(activity())
    applyUpdate.mockResolvedValue({
      data: { job_id: 'job-1', action: 'update', target_tag: 'v1.1.0' },
    })
    forcePendingUpdate.mockResolvedValue({
      data: { job_id: 'job-2', action: 'update', target_tag: 'v1.1.0' },
    })
    applyRollback.mockResolvedValue({
      data: { job_id: 'job-3', action: 'rollback', target_tag: 'v0.9.0' },
    })
    getUpdaterJob.mockReturnValue(new Promise(() => {}))
  })

  it('starts checked when the setting is on and sends backup=true', async () => {
    getUpdaterStatus.mockResolvedValue(updaterStatus())
    const w = await makeWrapper()
    await openDialog(w, 'Update to v1.1.0')
    expect(backupBox(w).element.checked).toBe(true)
    await submit(w)
    expect(applyUpdate).toHaveBeenCalledWith('pw', 'v1.1.0', false, true)
    w.unmount()
  })

  it('starts unchecked when the setting is off, notes the forced backup, and sends backup=false', async () => {
    getUpdaterStatus.mockResolvedValue(updaterStatus({ backup_default: false }))
    const w = await makeWrapper()
    await openDialog(w, 'Update to v1.1.0')
    expect(backupBox(w).element.checked).toBe(false)
    expect(w.find('[data-testid="update-backup-forced"]').exists()).toBe(true)
    await submit(w)
    expect(applyUpdate).toHaveBeenCalledWith('pw', 'v1.1.0', false, false)
    w.unmount()
  })

  it('the admin can override the default', async () => {
    getUpdaterStatus.mockResolvedValue(updaterStatus())
    const w = await makeWrapper()
    await openDialog(w, 'Update to v1.1.0')
    await backupBox(w).setValue(false)
    await submit(w)
    expect(applyUpdate).toHaveBeenCalledWith('pw', 'v1.1.0', false, false)
    w.unmount()
  })

  it('"Update now" on a postponed update starts from the stored choice and sends it', async () => {
    getUpdaterStatus.mockResolvedValue(updaterStatus({ backup_default: true }))
    getTransferActivity.mockResolvedValue(
      activity({
        target_tag: 'v1.1.0',
        deadline_iso: '2999-01-01T00:00:00',
        requested_by_id: 1,
        backup: false,
      }),
    )
    const w = await makeWrapper()
    await openDialog(w, 'Update now')
    expect(backupBox(w).element.checked).toBe(false)
    await submit(w)
    expect(forcePendingUpdate).toHaveBeenCalledWith('pw', false)
    w.unmount()
  })

  it('rollback offers no backup box', async () => {
    getUpdaterStatus.mockResolvedValue(updaterStatus())
    const w = await makeWrapper()
    await openDialog(w, 'Roll back')
    expect(backupBox(w).exists()).toBe(false)
    await submit(w)
    expect(applyRollback).toHaveBeenCalledWith('pw')
    w.unmount()
  })

  it('the job banner shows the phase, the backup directory and the warnings', async () => {
    getUpdaterStatus.mockResolvedValue(updaterStatus({ job_in_progress: 'job-1' }))
    getUpdaterJob.mockResolvedValue({
      data: {
        id: 'job-1',
        action: 'update',
        target_tag: 'v1.1.0',
        state: 'restarting',
        started_at: '',
        finished_at: null,
        log_tail: [],
        error: null,
        previous_tag: 'v1.0.0',
        rollback_reason: null,
        phase: 'syncing_infra',
        backup_dir: 'backups/pre-update/2026-09-26_120000_v1.0.0-to-v1.1.0',
        warnings: ['clamav did not come up healthy'],
      },
    })
    const w = await makeWrapper()
    const banner = w.find('.job-banner')
    expect(banner.text()).toContain(en.admin_system.update.phase.syncing_infra)
    expect(banner.text()).toContain('backups/pre-update/2026-09-26_120000_v1.0.0-to-v1.1.0')
    expect(banner.text()).toContain('clamav did not come up healthy')
    w.unmount()
  })

  it('an unknown phase from a newer executor is not rendered as a raw key', async () => {
    getUpdaterStatus.mockResolvedValue(updaterStatus({ job_in_progress: 'job-1' }))
    getUpdaterJob.mockResolvedValue({
      data: {
        id: 'job-1',
        action: 'update',
        target_tag: 'v1.1.0',
        state: 'pulling',
        started_at: '',
        finished_at: null,
        log_tail: [],
        error: null,
        previous_tag: 'v1.0.0',
        rollback_reason: null,
        phase: 'something_new',
      },
    })
    const w = await makeWrapper()
    expect(w.find('.job-banner').text()).not.toContain('admin_system.update.phase')
    expect(w.find('.job-phase').exists()).toBe(false)
    w.unmount()
  })
})
