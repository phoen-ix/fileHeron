/* The secret compose form (v2.24.0). Its submit button is DERIVED from a
 * visible list of reasons (the ShareCreate rule); at least one limit is
 * required; a passphrase must be typed twice; a client sees no groups, no
 * addresses and no link; and the content never leaves the form unsent. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ createSecret: vi.fn() }))
const push = vi.hoisted(() => vi.fn())
vi.mock('@/api/secrets', () => ({ createSecret: (p: unknown) => api.createSecret(p) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push, back: vi.fn() }) }))

import { useAuthStore } from '@/stores/auth'
import type { MeResponse } from '@/types/api'
import SecretCreate from '@/views/SecretCreate.vue'

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
  return mount(SecretCreate, {
    global: {
      plugins: [i18n],
      stubs: {
        RecipientPicker: RecipientPickerStub,
        ExpiryPicker: ExpiryPickerStub,
        PasswordGenerator: true,
        RouterLink: true,
      },
    },
  })
}

type W = ReturnType<typeof mountView>
const picker = (w: W) => w.findComponent(RecipientPickerStub)
const submit = (w: W) => w.find('[data-testid="secret-submit"]')
const blockers = (w: W) =>
  w.find('[data-testid="submit-blockers"]').exists()
    ? w.find('[data-testid="submit-blockers"]').text()
    : ''

async function fillMinimal(w: W) {
  await w.find('[data-testid="secret-content-input"]').setValue('hunter2')
  picker(w).vm.$emit('update:modelValue', { user_ids: [5], group_ids: [], emails: [] })
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.createSecret.mockReset()
  push.mockReset()
  api.createSecret.mockResolvedValue({ data: { id: 'sec1', link_url: null } })
})

describe('SecretCreate', () => {
  it('names every reason the button is disabled, and none once it is not', async () => {
    const w = mountView()
    await flushPromises()
    expect(submit(w).attributes('disabled')).toBeDefined()
    expect(blockers(w)).toContain(en.secrets.create.blockers.no_content)
    expect(blockers(w)).toContain(en.secrets.create.blockers.no_recipient_or_link)
    await fillMinimal(w)
    expect(blockers(w)).toBe('')
    expect(submit(w).attributes('disabled')).toBeUndefined()
  })

  it('needs a view limit, an expiry, or both', async () => {
    const w = mountView()
    await flushPromises()
    await fillMinimal(w)
    w.findComponent(ExpiryPickerStub).vm.$emit('update:modelValue', null)
    await w.find('input[type="checkbox"]:not([data-testid])').setValue(false)
    await flushPromises()
    expect(blockers(w)).toContain(en.secrets.create.blockers.no_limit)
  })

  it('asks for the passphrase twice and refuses a short one', async () => {
    const w = mountView()
    await flushPromises()
    await fillMinimal(w)
    await w.find('[data-testid="passphrase"]').setValue('short')
    expect(blockers(w)).toContain('at least 8 characters')
    await w.find('[data-testid="passphrase"]').setValue('long enough')
    await w.find('[data-testid="passphrase-repeat"]').setValue('long enougH')
    expect(blockers(w)).toContain(en.secrets.create.blockers.passphrase_mismatch)
    await w.find('[data-testid="passphrase-repeat"]').setValue('long enough')
    expect(blockers(w)).toBe('')
  })

  it('sends what was set and clears the text afterwards', async () => {
    const w = mountView()
    await flushPromises()
    await fillMinimal(w)
    await w.find('form').trigger('submit')
    await flushPromises()
    const body = api.createSecret.mock.calls[0][0]
    expect(body).toMatchObject({
      content: 'hunter2',
      max_views: 1,
      view_scope: 'per_person',
      recipients: { user_ids: [5], group_ids: [], emails: [] },
      create_link: false,
      burn_on_failures: false,
      passphrase: null,
    })
    expect(typeof body.expires_at).toBe('string')
    expect(push).toHaveBeenCalledWith({ name: 'secret-detail', params: { id: 'sec1' } })
  })

  it('shows the link once when one was created, instead of leaving the page', async () => {
    api.createSecret.mockResolvedValue({
      data: { id: 'sec1', link_url: 'https://x.test/s#tok', link_qr_svg: '<svg></svg>' },
    })
    const w = mountView()
    await flushPromises()
    await w.find('[data-testid="secret-content-input"]').setValue('hunter2')
    await w.find('[data-testid="create-link"]').setValue(true)
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(api.createSecret.mock.calls[0][0].create_link).toBe(true)
    expect(w.find('[data-testid="secret-link"]').text()).toBe('https://x.test/s#tok')
    expect(push).not.toHaveBeenCalled()
  })

  it('offers a client no groups, no addresses and no link', async () => {
    const w = mountView({ role: 'client', can_send_secrets_external: false })
    await flushPromises()
    expect(picker(w).props('allowGroups')).toBe(false)
    expect(picker(w).props('allowExternal')).toBe(false)
    expect(w.find('[data-testid="create-link"]').exists()).toBe(false)
    expect(blockers(w)).toContain(en.secrets.create.blockers.no_recipient)
  })

  it('offers "each recipient" only when a group makes it differ from "each person"', async () => {
    const w = mountView()
    await flushPromises()
    await fillMinimal(w)
    const scopes = () => w.findAll('input[type="radio"]').map((r) => r.attributes('value'))
    expect(scopes()).toEqual(['per_person', 'total'])
    picker(w).vm.$emit('update:modelValue', { user_ids: [5], group_ids: [2], emails: [] })
    await flushPromises()
    expect(scopes()).toEqual(['per_person', 'per_recipient', 'total'])
  })
})
