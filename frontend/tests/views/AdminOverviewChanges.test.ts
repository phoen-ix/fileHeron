/* The Overview's "Recently changed settings" panel: each row says when, who
 * and where - linked to the page that owns the setting - loads on its own, and
 * a failed load blanks this panel only. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ listSettingsChanges: vi.fn() }))
vi.mock('@/api/admin', () => ({
  adminListFiles: vi.fn(async () => ({ data: { total: 0 } })),
  getInboxUnreadCount: vi.fn(async () => ({ data: { unread: 0 } })),
  getSystemStatus: vi.fn(() => new Promise(() => {})),
  listIpBlocks: vi.fn(async () => ({ data: { total: 0 } })),
  listSettingsChanges: (n: number) => api.listSettingsChanges(n),
}))
vi.mock('@/api/shares', () => ({ listPendingApprovals: vi.fn(() => new Promise(() => {})) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

import AdminOverview from '@/views/AdminOverview.vue'
import { useAuthStore } from '@/stores/auth'

// Renders the target so the test can read where each row links.
const RouterLinkStub = defineComponent({
  props: { to: { type: Object, required: true } },
  setup: (props, { slots }) => () =>
    h('a', { 'data-to': JSON.stringify(props.to) }, slots.default?.()),
})

function row(id: number, over: Record<string, unknown>) {
  return {
    id, actor_user_id: 1, actor_display_name: 'Ada', actor_email: 'ada@test.local',
    target_type: 'settings', target_id: null, request_id: null, ip: null, extra: null,
    created_at: '2026-10-02T10:00:00', event_type: 'settings_changed', ...over,
  }
}

function mountPage() {
  const auth = useAuthStore()
  auth.user = { id: 1, role: 'admin', display_name: 'A' } as unknown as typeof auth.user
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminOverview, {
    global: {
      plugins: [i18n],
      stubs: { RouterLink: RouterLinkStub, AdminPageHeader: { template: '<header><slot /></header>' } },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.listSettingsChanges.mockReset()
})

describe('AdminOverview recently changed settings', () => {
  it('lists each change with who made it and a link to its page', async () => {
    api.listSettingsChanges.mockResolvedValue({
      data: {
        items: [
          row(3, { event_type: 'legal_changed', target_id: 'legal' }),
          row(2, { event_type: 'settings_changed', extra: { keys: ['branding.app_name'] }, actor_user_id: null, actor_display_name: null }),
          row(1, { event_type: 'smtp_config_changed', target_id: 'smtp', actor_user_id: 9, actor_display_name: null }),
        ],
      },
    })
    const w = mountPage()
    await flushPromises()
    expect(api.listSettingsChanges).toHaveBeenCalledWith(8)
    const rows = w.findAll('[data-testid="ov-changes"] li')
    expect(rows.length).toBe(3)
    const to = (i: number) => JSON.parse(rows[i].find('a').attributes('data-to')!)
    expect(to(0)).toEqual({ name: 'admin-settings-legal' })
    expect(to(1)).toEqual({ name: 'admin-settings-branding', hash: '#tunable-branding.app_name' })
    expect(rows[0].text()).toContain('Ada')
    expect(rows[1].text()).toContain(en.admin_overview.changes_system)
    expect(rows[1].text()).toContain('branding.app_name')
    expect(rows[2].text()).toContain(en.admin_audit.actor_deleted)
  })

  it('says so when nothing has changed yet', async () => {
    api.listSettingsChanges.mockResolvedValue({ data: { items: [] } })
    const w = mountPage()
    await flushPromises()
    expect(w.text()).toContain(en.admin_overview.changes_empty)
  })

  it('a failed load blanks this panel and nothing else', async () => {
    api.listSettingsChanges.mockRejectedValue(new Error('network'))
    const w = mountPage()
    await flushPromises()
    const panel = w.find('[aria-labelledby="ov-changes-h"]')
    expect(panel.text()).toContain(en.admin_overview.unavailable)
    expect(w.find('[aria-labelledby="ov-attention-h"]').text()).toContain(
      en.admin_overview.tile_quarantined,
    )
  })
})
