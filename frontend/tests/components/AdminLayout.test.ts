/* Mounts the REAL admin sidebar at desktop and narrow widths. v2.17.1 shipped
 * a sidebar that showed nothing but "Overview": a `v-if` added to the Overview
 * link captured the categories' `v-else`, and nothing rendered AdminLayout in
 * a test - the collapse state machine was tested through a bare harness and
 * the taxonomy through its data file, so the template that joins them had no
 * coverage at all. */

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ADMIN_NAV, ADMIN_OVERVIEW, ADMIN_ROUTE_NAMES } from '@/config/adminNav'
import en from '@/i18n/locales/en.json'
import { useAuthStore } from '@/stores/auth'
import type { AdminNavCollapseMode } from '@/types/api'

vi.mock('@/api/account')
vi.mock('@/api/admin', () => ({
  getInboxUnreadCount: vi.fn(async () => ({ data: { unread: 3 } })),
}))

import AdminLayout from '@/views/AdminLayout.vue'

const ME = {
  id: 1,
  email: 'admin@test.local',
  display_name: 'Admin',
  role: 'admin' as const,
  locale: 'en' as const,
  email_verified: true,
  is_disabled: false,
  created_at: '2026-06-06T00:00:00',
  last_login_at: null,
  quota_bytes: null,
  can_create_public_link: true,
  default_landing_page: null,
  home_page_enabled: true,
  requires_2fa: false,
  share_notify_recipients_default: true,
  can_change_own_email: false,
  file_preview_enabled: true,
  can_approve_shares: false,
  admin_nav_collapse_mode: 'accordion' as AdminNavCollapseMode | null,
  admin_nav_open_categories: null as string[] | null,
}

function lookup(obj: unknown, path: string): string {
  return String(
    path.split('.').reduce<unknown>(
      (acc, k) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[k] : undefined),
      obj,
    ),
  )
}

async function mountAt(routeName: string) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = { ...ME }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [...ADMIN_ROUTE_NAMES].map((n) => ({
      path: '/' + n,
      name: n,
      component: { template: '<div/>' },
    })),
  })
  await router.push({ name: routeName })
  await router.isReady()
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(AdminLayout, {
    global: { plugins: [pinia, router, i18n], stubs: { RouterView: true } },
  })
  await flushPromises()
  return w
}

function stubMatchMedia(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches,
      media: '(max-width: 720px)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  )
}

beforeEach(() => stubMatchMedia(false))
afterEach(() => vi.unstubAllGlobals())

describe('AdminLayout sidebar (desktop)', () => {
  it('renders the Overview link and EVERY category header, in order', async () => {
    const w = await mountAt(ADMIN_OVERVIEW.routeName)
    expect(w.find('.nav-link-top').text()).toBe(lookup(en, ADMIN_OVERVIEW.labelKey))
    const headers = w.findAll('.nav-cat-header').map((b) => b.text())
    expect(headers).toEqual(ADMIN_NAV.map((c) => lookup(en, c.labelKey)))
    expect(w.find('.nav-strip').exists()).toBe(false)
  })

  it('auto-expands the active route category and shows its links + the inbox badge', async () => {
    const w = await mountAt('admin-inbox')
    const open = w.findAll('.nav-cat-header').filter((b) => b.attributes('aria-expanded') === 'true')
    expect(open.map((b) => b.text())).toEqual([lookup(en, 'admin.nav_cat.email')])
    const links = w.findAll('.nav-cat-panel[data-open="true"] .nav-link').map((a) => a.text())
    expect(links.some((l) => l.startsWith(lookup(en, 'admin.nav.inbox')))).toBe(true)
    expect(w.find('.nav-badge').text()).toBe('3')
  })
})

describe('AdminLayout sidebar (≤720px)', () => {
  it('renders the category strip with an Overview chip and only the open category links', async () => {
    stubMatchMedia(true)
    const w = await mountAt('admin-mail-log')
    expect(w.find('.nav-cat-header').exists()).toBe(false)
    const chips = w.findAll('.nav-strip-cats .nav-chip').map((c) => c.text())
    expect(chips).toEqual([
      lookup(en, ADMIN_OVERVIEW.labelKey),
      ...ADMIN_NAV.map((c) => lookup(en, c.labelKey)),
    ])
    // The Inbox chip carries its unread badge ("Inbox 3"); compare labels only.
    const items = w
      .findAll('.nav-strip-items .nav-chip')
      .map((c) => c.text().replace(/\s*\d+$/, ''))
    const email = ADMIN_NAV.find((c) => c.key === 'email')!
    expect(items).toEqual(email.items.map((i) => lookup(en, i.labelKey)))
    expect(w.find('.nav-strip-items .nav-badge').text()).toBe('3')
  })
})
