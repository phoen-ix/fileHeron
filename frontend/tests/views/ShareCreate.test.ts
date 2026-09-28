/* "Create + send" used to go grey with nothing on the page saying why - a typed
 * but unpicked recipient was the case that prompted this (2026-09-28). The
 * button's state is now derived from a visible list of reasons, and a share to
 * an address with no account always carries the public link that reaches it. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  createShare: vi.fn(),
  registerFilesAdded: vi.fn(),
  inviteUser: vi.fn(),
}))
vi.mock('@/api/shares', () => ({
  createShare: (p: unknown) => api.createShare(p),
  registerFilesAdded: (...a: unknown[]) => api.registerFilesAdded(...a),
}))
vi.mock('@/api/account', () => ({ inviteUser: (p: unknown) => api.inviteUser(p) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn(), back: vi.fn() }) }))
vi.mock('@/composables/useUploadLeaveGuard', () => ({ useUploadLeaveGuard: () => {} }))
vi.mock('@/composables/useUpload', async () => {
  const { computed, ref } = await import('vue')
  const items = ref<unknown[]>([])
  return {
    __items: items,
    useUpload: () => ({
      items,
      isActive: computed(() => false),
      log: ref([]),
      add: vi.fn(),
      remove: vi.fn(),
      retry: vi.fn(),
      reset: vi.fn(),
      start: vi.fn(async () => {}),
    }),
  }
})

import * as uploadModule from '@/composables/useUpload'
import { useAuthStore } from '@/stores/auth'
import type { MeResponse } from '@/types/api'
import ShareCreate from '@/views/ShareCreate.vue'

const uploadItems = (uploadModule as unknown as { __items: { value: unknown[] } }).__items

const RecipientPickerStub = defineComponent({
  name: 'RecipientPicker',
  props: ['modelValue', 'disabled', 'allowExternal', 'canPublicLink'],
  emits: ['update:modelValue', 'update:pending'],
  setup: () => () => h('div', { class: 'recipient-picker-stub' }),
})
const ExpiryPickerStub = defineComponent({
  name: 'ExpiryPicker',
  emits: ['update:modelValue'],
  mounted() {
    this.$emit('update:modelValue', '2030-01-01T10:00')
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
    can_create_public_link: true,
    can_share_external: false,
    offer_invite_on_external: false,
    share_notify_recipients_default: true,
    ...user,
  } as MeResponse
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(ShareCreate, {
    global: {
      plugins: [i18n],
      stubs: {
        RecipientPicker: RecipientPickerStub,
        ExpiryPicker: ExpiryPickerStub,
        FileUploadArea: true,
        ShareUploadProgress: true,
      },
    },
  })
}

const picker = (w: ReturnType<typeof mountView>) => w.findComponent(RecipientPickerStub)
const submitBtn = (w: ReturnType<typeof mountView>) => w.find('button[type="submit"]')
const blockerText = (w: ReturnType<typeof mountView>) =>
  w.find('[data-testid="submit-blockers"]').exists()
    ? w.find('[data-testid="submit-blockers"]').text()
    : ''

function queueOneFile() {
  uploadItems.value = [{ uid: 'u1', file: { name: 'a.bin', size: 1 }, state: 'queued' }]
}

beforeEach(() => {
  setActivePinia(createPinia())
  uploadItems.value = []
  api.createShare.mockReset()
  api.inviteUser.mockReset()
  api.registerFilesAdded.mockReset()
  api.registerFilesAdded.mockResolvedValue({ data: {} })
  api.createShare.mockResolvedValue({ data: { id: 's1', state: 'active', public_link: null } })
  api.inviteUser.mockResolvedValue({ data: { ok: true } })
})

describe('ShareCreate', () => {
  it('lists every reason the button is disabled, and none once it is not', async () => {
    const w = mountView()
    await flushPromises()
    expect(submitBtn(w).attributes('disabled')).toBeDefined()
    expect(blockerText(w)).toContain(en.share_create.blockers.no_files)
    expect(blockerText(w)).toContain(en.share_create.blockers.no_recipient_or_link)
    expect(submitBtn(w).attributes('aria-describedby')).toBe('share-create-blockers')

    queueOneFile()
    picker(w).vm.$emit('update:pending', 'michael.fiedler@syncore.at')
    await flushPromises()
    expect(submitBtn(w).attributes('disabled')).toBeDefined()
    expect(blockerText(w)).toContain('michael.fiedler@syncore.at')
    expect(blockerText(w)).not.toContain(en.share_create.blockers.no_files)

    picker(w).vm.$emit('update:pending', '')
    picker(w).vm.$emit('update:modelValue', { user_ids: [5], group_ids: [], emails: [] })
    await flushPromises()
    expect(w.find('[data-testid="submit-blockers"]').exists()).toBe(false)
    expect(submitBtn(w).attributes('disabled')).toBeUndefined()
  })

  it('an address without an account locks the public link on and sends it', async () => {
    const w = mountView({ can_share_external: true })
    await flushPromises()
    expect(picker(w).props('allowExternal')).toBe(true)

    queueOneFile()
    picker(w).vm.$emit('update:modelValue', {
      user_ids: [],
      group_ids: [],
      emails: ['ext@example.com'],
    })
    await flushPromises()

    const toggle = w.find('.public-link-section input[type="checkbox"]')
    expect((toggle.element as HTMLInputElement).checked).toBe(true)
    expect(toggle.attributes('disabled')).toBeDefined()
    expect(w.text()).toContain(en.share_create.public_link.required_for_external)

    await w.find('form').trigger('submit')
    await flushPromises()
    const payload = api.createShare.mock.calls[0][0]
    expect(payload.recipients.emails).toEqual(['ext@example.com'])
    expect(payload.public_link).not.toBeNull()
  })

  it('warns that a quiet share reaches no address without an account', async () => {
    const w = mountView({ can_share_external: true })
    await flushPromises()
    picker(w).vm.$emit('update:modelValue', {
      user_ids: [],
      group_ids: [],
      emails: ['ext@example.com'],
    })
    await flushPromises()
    expect(w.find('[data-testid="external-quiet"]').exists()).toBe(false)
    await w.find('.notify-recipients-section input[type="checkbox"]').setValue(false)
    expect(w.find('[data-testid="external-quiet"]').exists()).toBe(true)
  })

  it('asks about invites only when the admin turned it on, and invites only the ticked', async () => {
    const plain = mountView({ can_share_external: true })
    await flushPromises()
    picker(plain).vm.$emit('update:modelValue', {
      user_ids: [],
      group_ids: [],
      emails: ['a@example.com'],
    })
    await flushPromises()
    expect(plain.find('[data-testid="invite-offer"]').exists()).toBe(false)
    plain.unmount()

    const w = mountView({ can_share_external: true, offer_invite_on_external: true })
    await flushPromises()
    queueOneFile()
    picker(w).vm.$emit('update:modelValue', {
      user_ids: [],
      group_ids: [],
      emails: ['a@example.com', 'b@example.com'],
    })
    await flushPromises()
    const offer = w.find('[data-testid="invite-offer"]')
    expect(offer.exists()).toBe(true)
    const boxes = offer.findAll('input[type="checkbox"]')
    expect(boxes).toHaveLength(2)
    expect(boxes.every((b) => !(b.element as HTMLInputElement).checked)).toBe(true)

    await boxes[1].setValue(true)
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(api.inviteUser).toHaveBeenCalledTimes(1)
    expect(api.inviteUser.mock.calls[0][0]).toMatchObject({
      email: 'b@example.com',
      target_role: 'client',
    })
  })
})
