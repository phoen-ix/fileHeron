/* The share page lists the addresses without an account that the public link
 * was mailed to. Mounted for real: a template change that only unit tests and
 * a type check had seen shipped once already (v2.17.1's sidebar), and this
 * block sits between two existing v-if blocks. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ getShare: vi.fn() }))
vi.mock('@/api/shares', () => ({
  getShare: (id: string) => api.getShare(id),
  approveShare: vi.fn(),
  decideAddedFiles: vi.fn(),
  deleteShare: vi.fn(),
  expireShareNow: vi.fn(),
  registerFilesAdded: vi.fn(),
  rejectShare: vi.fn(),
  resubmitShare: vi.fn(),
  updateShareDownloadLimit: vi.fn(),
  updateShareExpiry: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 's1' } }) }))
vi.mock('@/composables/useUploadLeaveGuard', () => ({ useUploadLeaveGuard: () => {} }))
vi.mock('@/composables/useUpload', async () => {
  const { computed, ref } = await import('vue')
  return {
    useUpload: () => ({
      items: ref([]),
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

import { useAuthStore } from '@/stores/auth'
import type { MeResponse, ShareResponse } from '@/types/api'
import ShareDetail from '@/views/ShareDetail.vue'

function share(over: Partial<ShareResponse> = {}): ShareResponse {
  return {
    id: 's1',
    kind: 'outbound',
    state: 'active',
    subject: 'Q3 figures',
    effective_subject: 'Q3 figures',
    message: 'See attached.',
    created_at: '2026-09-28T08:00:00',
    expires_at: null,
    created_by_id: 1,
    recipient_user_ids: [],
    recipient_groups: [{ id: 3, name: 'Finance', is_company_inbox: false }],
    files: [],
    download_limit: null,
    downloads_remaining: null,
    public_link: null,
    rejection_reason: null,
    approval_decided_at: null,
    viewer_can_approve: false,
    public_link_summary: null,
    content_fingerprint: null,
    files_awaiting_review: [],
    external_recipients: [],
    ...over,
  } as ShareResponse
}

async function mountPage(data: ShareResponse) {
  api.getShare.mockResolvedValue({ data })
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(ShareDetail, {
    global: {
      plugins: [i18n],
      stubs: {
        FileRow: true,
        PublicLinkPanel: true,
        FileUploadArea: true,
        ExpiryPicker: true,
        FilePreviewModal: true,
        RouterLink: true,
      },
    },
  })
  await flushPromises()
  return w
}

beforeEach(() => {
  setActivePinia(createPinia())
  useAuthStore().user = { id: 1, role: 'employee' } as MeResponse
  api.getShare.mockReset()
})

describe('ShareDetail - recipients without an account', () => {
  it('lists the addresses the link was mailed to, beside the groups and message', async () => {
    const w = await mountPage(
      share({
        external_recipients: ['ext@example.com', 'two@example.com'],
        external_recipients_emailed: true,
      }),
    )

    const block = w.find('[data-testid="external-recipients"]')
    expect(block.exists()).toBe(true)
    expect(block.text()).toContain(en.share_detail.external_recipients)
    expect(block.text()).toContain('ext@example.com')
    expect(block.text()).toContain('two@example.com')
    expect(block.text()).toContain(en.share_detail.external_emailed)
    // The neighbouring blocks still render - the new v-if did not capture them.
    expect(w.text()).toContain('Finance')
    expect(w.find('.message').text()).toBe('See attached.')
  })

  it('says when the sender chose to send the link themselves', async () => {
    const w = await mountPage(
      share({ external_recipients: ['ext@example.com'], external_recipients_emailed: false }),
    )
    const block = w.find('[data-testid="external-recipients"]')
    expect(block.text()).toContain(en.share_detail.external_not_emailed)
  })

  it('renders nothing when there are none, or the viewer is not shown them', async () => {
    const w = await mountPage(share({ external_recipients: [] }))
    expect(w.find('[data-testid="external-recipients"]').exists()).toBe(false)
    expect(w.find('.message').exists()).toBe(true)

    const older = await mountPage(share({ external_recipients: undefined }))
    expect(older.find('[data-testid="external-recipients"]').exists()).toBe(false)
  })

  it('shows a preset expiry that has not started as counting from ready', async () => {
    const w = await mountPage(share({ expires_at: null, expires_in_sec: 3600 }))
    expect(w.text()).toContain('1 hour after the files are ready')
    expect(w.text()).not.toContain(en.expiry.never_label)
  })
})
