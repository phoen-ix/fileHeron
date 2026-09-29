/* Config backup: export reads secrets back out and import replaces the
 * configuration, so both confirm the admin's own password - in StepUpDialog,
 * not in a field on the page. For import the dialog is also the "are you
 * sure": it states what the import destroys, so there is one dialog, not a
 * yes/no confirm followed by a password. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const exportConfigBackup = vi.fn()
const importConfigBackup = vi.fn()
const previewBackupImport = vi.fn()
vi.mock('@/api/admin', () => ({
  exportConfigBackup: (p: unknown) => exportConfigBackup(p),
  importConfigBackup: (...a: unknown[]) => importConfigBackup(...a),
  previewBackupImport: (...a: unknown[]) => previewBackupImport(...a),
}))
vi.mock('@/utils/downloadBlob', () => ({ downloadBlob: vi.fn() }))

const confirm = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast: vi.fn(), confirm }) }))

import AdminSettingsBackup from '@/views/AdminSettingsBackup.vue'

function summary(over: Record<string, unknown> = {}) {
  return {
    dry_run: true,
    secret_mode: 'passphrase',
    categories: ['groups'],
    shares_to_invalidate: 7,
    files_deleted: 0,
    counts: {},
    purged_users: [],
    purged_groups: [],
    sessions_revoked: 0,
    env_snapshot_present: false,
    env_dotenv: null,
    version_warning: null,
    warnings: [],
    ...over,
  }
}

function mountIt() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSettingsBackup, {
    global: {
      plugins: [i18n],
      stubs: { AdminPageHeader: { template: '<div><slot /><slot name="actions" /></div>' } },
    },
  })
}

type W = ReturnType<typeof mountIt>
const dialog = (w: W) => w.find('[data-testid="step-up-dialog"]')

async function confirmIn(w: W, pw: string) {
  await w.find('[data-testid="step-up-password"]').setValue(pw)
  await dialog(w).find('form').trigger('submit')
  await flushPromises()
}

async function previewAFile(w: W) {
  const input = w.find<HTMLInputElement>('input[type="file"]')
  const file = new File(['{}'], 'x.fhbackup.json', { type: 'application/json' })
  Object.defineProperty(input.element, 'files', { value: [file] })
  await input.trigger('change')
  await w.findAll('button').find((b) => b.text() === en.admin_backup.preview_cta)!.trigger('click')
  await flushPromises()
}

describe('AdminSettingsBackup asks for the password in a dialog', () => {
  beforeEach(() => {
    exportConfigBackup.mockReset()
    importConfigBackup.mockReset()
    previewBackupImport.mockReset()
    confirm.mockReset()
  })

  it('has no password field on the page', () => {
    const w = mountIt()
    expect(w.find('input[autocomplete="current-password"]').exists()).toBe(false)
  })

  it('export: the button opens the dialog, and the password goes to the export', async () => {
    exportConfigBackup.mockResolvedValue({ data: new Blob(['{}']) })
    const w = mountIt()
    // The default secret mode is passphrase; satisfy its own (separate) rule.
    const [pp, ppConfirm] = w.findAll('input[type="password"]')
    await pp.setValue('a-long-passphrase')
    await ppConfirm.setValue('a-long-passphrase')
    await w.find('[data-testid="backup-export"]').trigger('click')
    expect(dialog(w).exists()).toBe(true)
    expect(exportConfigBackup).not.toHaveBeenCalled()
    await confirmIn(w, 'my-pw')
    expect(exportConfigBackup).toHaveBeenCalledWith(expect.objectContaining({ password: 'my-pw' }))
    expect(dialog(w).exists()).toBe(false)
  })

  it('import: one danger dialog states the cost and takes the password', async () => {
    previewBackupImport.mockResolvedValue({ data: summary() })
    importConfigBackup.mockResolvedValue({ data: summary({ dry_run: false }) })
    const w = mountIt()
    await previewAFile(w)
    await w.find('[data-testid="backup-import"]').trigger('click')
    expect(dialog(w).text()).toContain(en.admin_backup.confirm_title)
    expect(dialog(w).text()).toContain('7')
    expect(dialog(w).find('[data-testid="step-up-confirm"]').classes()).toContain('fh-btn-danger')
    await confirmIn(w, 'my-pw')
    expect(confirm).not.toHaveBeenCalled()
    expect(importConfigBackup).toHaveBeenCalledWith(expect.any(File), undefined, 'my-pw')
    expect(dialog(w).exists()).toBe(false)
  })

  it('a wrong password on import stays in the dialog', async () => {
    previewBackupImport.mockResolvedValue({ data: summary() })
    importConfigBackup.mockRejectedValue({
      isAxiosError: true,
      response: { status: 403, data: { code: 'INVALID_PASSWORD', error: 'Password incorrect.' } },
    })
    const w = mountIt()
    await previewAFile(w)
    await w.find('[data-testid="backup-import"]').trigger('click')
    await confirmIn(w, 'nope')
    expect(dialog(w).exists()).toBe(true)
    expect(dialog(w).find('[role="alert"]').exists()).toBe(true)
  })
})
