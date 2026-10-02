/* The encryption-at-rest page: turning it on needs the key-custody
 * acknowledgement before the password prompt opens (the backend refuses
 * without it too), and both directions go through the step-up dialog. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), retry: vi.fn() }))
vi.mock('@/api/admin', () => ({
  getEncryptionSettings: () => api.get(),
  updateEncryptionSettings: (body: unknown) => api.put(body),
  retryFailedEncryption: () => api.retry(),
}))

import { useUiStore } from '@/stores/ui'
import AdminSettingsEncryption from '@/views/AdminSettingsEncryption.vue'

const StepUpStub = defineComponent({
  props: {
    open: Boolean,
    message: String,
    error: { type: String, default: null },
    confirmLabel: String,
  },
  emits: ['confirm', 'cancel'],
  template: `<div v-if="open" data-testid="stepup">
    <p class="msg">{{ message }}</p><p class="err">{{ error }}</p>
    <button class="go" @click="$emit('confirm', 'pw')">{{ confirmLabel }}</button></div>`,
})

function status(over: Record<string, unknown> = {}) {
  return {
    enabled: false,
    backend: 'local',
    files: { encrypted: 0, plaintext: 3, plaintext_bytes: 2048, awaiting_encryption: 0 },
    inbound_attachments: { encrypted: 0, plaintext: 1 },
    pending_purges: 0,
    failed_purges: 0,
    last_run: null,
    deferred: 0,
    backfill_task_enabled: true,
    ...over,
  }
}

function mountPage() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSettingsEncryption, {
    global: {
      plugins: [i18n],
      stubs: {
        AdminPageHeader: { template: '<header><slot /></header>' },
        RouterLink: { template: '<a><slot /></a>' },
        StepUpDialog: StepUpStub,
      },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.get.mockReset()
  api.put.mockReset()
  api.retry.mockReset()
})

describe('AdminSettingsEncryption', () => {
  it('asks for the acknowledgement before the password, then turns it on', async () => {
    api.get.mockResolvedValue({ data: status() })
    api.put.mockResolvedValue({ data: status({ enabled: true }) })
    const w = mountPage()
    await flushPromises()
    const toast = vi.spyOn(useUiStore(), 'pushToast')

    expect(w.find('[data-testid="encryption-state"]').text()).toBe(en.admin_encryption.state_off)
    expect(w.find('[data-testid="encryption-files-plaintext"]').text()).toContain('3')
    const on = w.find('[data-testid="encryption-turn-on"]')
    expect(on.attributes('disabled')).toBeDefined()

    await w.find('[data-testid="encryption-ack"]').setValue(true)
    expect(on.attributes('disabled')).toBeUndefined()
    await on.trigger('click')
    expect(w.find('[data-testid="stepup"] .msg').text()).toBe(en.admin_encryption.password_on)

    await w.find('[data-testid="stepup"] .go').trigger('click')
    await flushPromises()
    expect(api.put).toHaveBeenCalledWith({
      enabled: true,
      acknowledge_key_custody: true,
      password: 'pw',
    })
    expect(w.find('[data-testid="stepup"]').exists()).toBe(false)
    expect(w.find('[data-testid="encryption-state"]').text()).toBe(en.admin_encryption.state_on)
    expect(w.find('[data-testid="encryption-custody"]').exists()).toBe(false)
    expect(toast).toHaveBeenCalledWith(en.admin_encryption.saved_on, 'success')
  })

  it('turns it off through the password prompt, without any acknowledgement', async () => {
    api.get.mockResolvedValue({ data: status({ enabled: true }) })
    api.put.mockResolvedValue({ data: status() })
    const w = mountPage()
    await flushPromises()

    expect(w.find('[data-testid="encryption-custody"]').exists()).toBe(false)
    await w.find('[data-testid="encryption-turn-off"]').trigger('click')
    expect(w.find('[data-testid="stepup"] .msg').text()).toBe(en.admin_encryption.password_off)
    await w.find('[data-testid="stepup"] .go').trigger('click')
    await flushPromises()
    expect(api.put).toHaveBeenCalledWith({
      enabled: false,
      acknowledge_key_custody: undefined,
      password: 'pw',
    })
    expect(w.find('[data-testid="encryption-state"]').text()).toBe(en.admin_encryption.state_off)
  })

  it('shows a wrong password inside the dialog and keeps it open', async () => {
    api.get.mockResolvedValue({ data: status({ enabled: true }) })
    api.put.mockRejectedValue({
      isAxiosError: true,
      response: { status: 403, data: { error: 'Wrong password', code: 'INVALID_PASSWORD' } },
    })
    const w = mountPage()
    await flushPromises()
    await w.find('[data-testid="encryption-turn-off"]').trigger('click')
    await w.find('[data-testid="stepup"] .go').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="stepup"]').exists()).toBe(true)
    expect(w.find('[data-testid="stepup"] .err').text()).not.toBe('')
    expect(w.find('[data-testid="encryption-state"]').text()).toBe(en.admin_encryption.state_on)
  })

  it('names the versioned-bucket caveat only on object storage', async () => {
    api.get.mockResolvedValue({ data: status() })
    let w = mountPage()
    await flushPromises()
    expect(w.text()).not.toContain(en.admin_encryption.s3_note)
    api.get.mockResolvedValue({ data: status({ backend: 's3' }) })
    w = mountPage()
    await flushPromises()
    expect(w.text()).toContain(en.admin_encryption.s3_note)
    expect(w.text()).toContain(en.admin_encryption.backend.s3)
  })

  it('shows replaced copies awaiting deletion only when there are some', async () => {
    api.get.mockResolvedValue({ data: status() })
    let w = mountPage()
    await flushPromises()
    expect(w.text()).not.toContain(en.admin_encryption.pending_purges)
    api.get.mockResolvedValue({ data: status({ pending_purges: 4, failed_purges: 1 }) })
    w = mountPage()
    await flushPromises()
    expect(w.text()).toContain(en.admin_encryption.pending_purges)
    expect(w.text()).toContain(en.admin_encryption.failed_purges)
  })

  it('summarises the last background run, or says there was none', async () => {
    api.get.mockResolvedValue({ data: status() })
    let w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="encryption-last-run"]').text()).toBe(
      en.admin_encryption.last_run_never,
    )
    api.get.mockResolvedValue({
      data: status({
        last_run: {
          finished_at: '2026-10-02T10:00:00',
          encrypted: 5,
          failed: 1,
          deferred: 0,
          skipped: 0,
          remaining: 7,
          stopped: 'insufficient_space',
        },
      }),
    })
    w = mountPage()
    await flushPromises()
    const text = w.find('[data-testid="encryption-last-run"]').text()
    expect(text).toContain('5 encrypted, 7 still to do')
    expect(text).toContain('1 failed')
    expect(text).toContain(en.admin_encryption.stopped_insufficient_space)
  })

  it('offers to retry the set-aside files, and says so when it cannot count them', async () => {
    api.get.mockResolvedValue({ data: status({ deferred: 2 }) })
    api.retry.mockResolvedValue({ data: status({ deferred: 0 }) })
    const w = mountPage()
    await flushPromises()
    const toast = vi.spyOn(useUiStore(), 'pushToast')
    await w.find('[data-testid="encryption-retry"]').trigger('click')
    await flushPromises()
    expect(api.retry).toHaveBeenCalledOnce()
    expect(toast).toHaveBeenCalledWith(en.admin_encryption.retry_toast, 'success')
    expect(w.find('[data-testid="encryption-deferred"]').exists()).toBe(false)

    api.get.mockResolvedValue({ data: status({ deferred: null }) })
    const w2 = mountPage()
    await flushPromises()
    expect(w2.find('[data-testid="encryption-deferred"]').text()).toBe(
      en.admin_encryption.deferred_unknown,
    )
    expect(w2.find('[data-testid="encryption-retry"]').exists()).toBe(false)
  })

  it('warns when the switch is on but the background task is off', async () => {
    api.get.mockResolvedValue({ data: status({ enabled: true, backfill_task_enabled: false }) })
    let w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="encryption-task-off"]').exists()).toBe(true)
    api.get.mockResolvedValue({ data: status({ enabled: false, backfill_task_enabled: false }) })
    w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="encryption-task-off"]').exists()).toBe(false)
  })
})
