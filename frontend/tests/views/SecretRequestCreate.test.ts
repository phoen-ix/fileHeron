/* The secret-request compose form (v2.24.0). Submit is DERIVED from visible
 * blockers (the ShareCreate rule); a request needs something to ask for, someone
 * to ask, and at least one limit for the answer; the passphrase is typed twice. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ createSecretRequest: vi.fn() }))
const push = vi.hoisted(() => vi.fn())
vi.mock('@/api/secretRequests', () => ({
  createSecretRequest: (p: unknown) => api.createSecretRequest(p),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push, back: vi.fn() }) }))

import { useAuthStore } from '@/stores/auth'
import type { MeResponse } from '@/types/api'
import SecretRequestCreate from '@/views/SecretRequestCreate.vue'

const RecipientPickerStub = defineComponent({
  name: 'RecipientPicker',
  props: ['modelValue', 'disabled', 'allowExternal', 'allowGroups', 'isAdmin', 'purpose'],
  emits: ['update:modelValue', 'update:pending'],
  setup: () => () => h('div', { class: 'recipient-picker-stub' }),
})
const ExpiryPickerStub = defineComponent({
  name: 'ExpiryPicker',
  props: ['modelValue', 'presets', 'disabled'],
  emits: ['update:modelValue'],
  mounted() {
    this.$emit('update:modelValue', '2030-01-01T10:00:00')
  },
  render: () => h('div'),
})

function mountView(user: Partial<MeResponse> = {}) {
  const auth = useAuthStore()
  auth.user = {
    id: 1,
    email: 'emp@test.local',
    display_name: 'Emp',
    role: 'employee',
    secrets_enabled: true,
    can_send_secrets: true,
    can_send_secrets_external: true,
    secret_limits: {
      max_views: 100,
      max_expiry_days: 9999,
      max_lifetime_days: 90,
      passphrase_failure_mode: 'lock',
      passphrase_max_failures: 10,
    },
    ...user,
  } as MeResponse
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(SecretRequestCreate, {
    global: {
      plugins: [i18n],
      stubs: {
        RecipientPicker: RecipientPickerStub,
        ExpiryPicker: ExpiryPickerStub,
        RouterLink: true,
      },
    },
  })
}

type W = ReturnType<typeof mountView>
const submit = (w: W) => w.find('[data-testid="request-submit"]')
const blockers = (w: W) =>
  w.find('[data-testid="request-blockers"]').exists()
    ? w.find('[data-testid="request-blockers"]').text()
    : ''

beforeEach(() => {
  setActivePinia(createPinia())
  api.createSecretRequest.mockReset()
  push.mockReset()
})

describe('SecretRequestCreate', () => {
  it('says what is missing and keeps submit disabled until it is there', async () => {
    const w = mountView()
    await flushPromises()
    expect(submit(w).attributes('disabled')).toBeDefined()
    expect(blockers(w)).toContain(en.secret_requests.create.blockers.no_label)
    expect(blockers(w)).toContain(en.secret_requests.create.blockers.no_target_or_link)
    await w.find('[data-testid="request-label"]').setValue('Router password')
    await w.find('[data-testid="request-create-link"]').setValue(true)
    expect(blockers(w)).toBe('')
    expect(submit(w).attributes('disabled')).toBeUndefined()
  })

  it('needs at least one limit for the answer', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-testid="request-label"]').setValue('Router password')
    await w.find('[data-testid="request-create-link"]').setValue(true)
    await w.find('[data-testid="request-limit-views"]').setValue(false)
    const lifetime = w.find('[data-testid="request-lifetime"]')
    const select = lifetime.element as HTMLSelectElement
    select.selectedIndex = select.options.length - 1 // "No time limit"
    await lifetime.trigger('change')
    expect(submit(w).attributes('disabled')).toBeDefined()
    expect(blockers(w)).toContain(en.secret_requests.create.blockers.no_limit)
  })

  it('sends the request with the answer terms and a passphrase typed twice', async () => {
    api.createSecretRequest.mockResolvedValue({ data: { id: 'r1', link_url: null } })
    const w = mountView()
    await flushPromises()
    await w.find('[data-testid="request-label"]').setValue('Router password')
    await w.find('[data-testid="request-note"]').setValue('The admin one.')
    await w.find('[data-testid="request-create-link"]').setValue(true)
    await w.find('[data-testid="request-passphrase"]').setValue('mine12345')
    expect(blockers(w)).toContain(en.secrets.create.blockers.passphrase_mismatch)
    await w.find('[data-testid="request-passphrase-repeat"]').setValue('mine12345')
    await w.find('form').trigger('submit')
    await flushPromises()
    const payload = api.createSecretRequest.mock.calls[0][0]
    expect(payload).toMatchObject({
      label: 'Router password',
      note: 'The admin one.',
      answer_max_views: 1,
      answer_expires_in_sec: 7 * 86400,
      passphrase: 'mine12345',
      create_link: true,
    })
    expect(payload.expires_at).toMatch(/^2030-01-01T/)
    expect(push).toHaveBeenCalledWith({ name: 'secret-request-detail', params: { id: 'r1' } })
  })

  it('offers a client no link', async () => {
    const w = mountView({ role: 'client', can_send_secrets_external: false })
    await flushPromises()
    expect(w.find('[data-testid="request-create-link"]').exists()).toBe(false)
    expect(blockers(w)).toContain(en.secret_requests.create.blockers.no_target)
  })
})
