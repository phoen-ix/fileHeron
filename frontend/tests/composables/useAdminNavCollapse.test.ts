/* Unit tests for the admin-sidebar collapse state machine. Mounts a tiny
 * harness with a real memory router + i18n + pinia; only `@/api/account` is
 * mocked so toggles don't hit the network. */

import { defineComponent, h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as accountApi from '@/api/account'
import { ADMIN_NAV } from '@/config/adminNav'
import { useAdminNavCollapse } from '@/composables/useAdminNavCollapse'
import en from '@/i18n/locales/en.json'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type { AdminNavCollapseMode } from '@/types/api'

vi.mock('@/api/account')

const BASE_ME = {
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
  admin_nav_collapse_mode: null as AdminNavCollapseMode | null,
  admin_nav_open_categories: null as string[] | null,
}

// Shared mutable "server" state so getMe echoes back what the last persist
// wrote (mirrors how the real backend + refreshMe stay in sync).
let currentMe: typeof BASE_ME

const allNames = [
  ...new Set(ADMIN_NAV.flatMap((c) => c.items.flatMap((i) => i.matchNames))),
]

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'root', component: { template: '<div/>' } },
      ...allNames.map((n) => ({
        path: '/' + n,
        name: n,
        component: { template: '<div/>' },
      })),
    ],
  })
}

type Api = ReturnType<typeof useAdminNavCollapse>

async function setup(
  me: Partial<typeof BASE_ME>,
  routeName: string,
): Promise<{ api: Api; pinia: Pinia; router: Router }> {
  const pinia = createPinia()
  setActivePinia(pinia)
  currentMe = { ...BASE_ME, ...me }
  const auth = useAuthStore()
  auth.user = { ...currentMe }

  const router = makeRouter()
  await router.push({ name: routeName })
  await router.isReady()

  let api!: Api
  const Harness = defineComponent({
    setup() {
      api = useAdminNavCollapse()
      return () => h('div')
    },
  })
  mount(Harness, { global: { plugins: [pinia, router, makeI18n()] } })
  await nextTick()
  return { api, pinia, router }
}

function makeI18n() {
  return createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
}

beforeEach(() => {
  vi.mocked(accountApi.getMe).mockImplementation(
    async () => ({ data: currentMe }) as never,
  )
  vi.mocked(accountApi.updateAdminNavOpenCategories).mockImplementation(
    async (open: string[]) => {
      currentMe = { ...currentMe, admin_nav_open_categories: open }
      return { data: currentMe } as never
    },
  )
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('initial open-set', () => {
  it('accordion + null persisted opens nothing (no active category)', async () => {
    const { api } = await setup({ admin_nav_collapse_mode: 'accordion' }, 'root')
    expect(api.isOpen('access')).toBe(false)
    expect(api.isOpen('system')).toBe(false)
  })

  it('expanded + null persisted opens every category', async () => {
    const { api } = await setup({ admin_nav_collapse_mode: 'expanded' }, 'root')
    for (const c of ['access', 'sharing', 'messaging', 'system'] as const) {
      expect(api.isOpen(c)).toBe(true)
    }
  })

  it('honors a non-null persisted set verbatim', async () => {
    const { api } = await setup(
      { admin_nav_collapse_mode: 'accordion', admin_nav_open_categories: ['sharing'] },
      'root',
    )
    expect(api.isOpen('sharing')).toBe(true)
    expect(api.isOpen('access')).toBe(false)
  })
})

describe('navigation auto-expand', () => {
  it('accordion opens only the active route category', async () => {
    const { api } = await setup({ admin_nav_collapse_mode: 'accordion' }, 'admin-mail-log')
    expect(api.isOpen('messaging')).toBe(true)
    expect(api.isOpen('access')).toBe(false)
  })

  it('manual keeps prior categories open when navigating', async () => {
    const { api } = await setup(
      { admin_nav_collapse_mode: 'manual', admin_nav_open_categories: ['access'] },
      'admin-mail-log',
    )
    expect(api.isOpen('access')).toBe(true)
    expect(api.isOpen('messaging')).toBe(true)
  })

  it('maps a detail route to its parent category', async () => {
    const { api } = await setup({ admin_nav_collapse_mode: 'accordion' }, 'admin-user-detail')
    expect(api.isOpen('access')).toBe(true)
  })
})

describe('toggle', () => {
  it('accordion keeps at most one open and persists', async () => {
    const { api } = await setup({ admin_nav_collapse_mode: 'accordion' }, 'root')
    await api.toggle('access')
    expect(api.isOpen('access')).toBe(true)

    await api.toggle('sharing')
    expect(api.isOpen('sharing')).toBe(true)
    expect(api.isOpen('access')).toBe(false)

    await api.toggle('sharing')
    expect(api.isOpen('sharing')).toBe(false)

    expect(accountApi.updateAdminNavOpenCategories).toHaveBeenLastCalledWith([])
  })

  it('manual toggles categories independently', async () => {
    const { api } = await setup({ admin_nav_collapse_mode: 'manual' }, 'root')
    await api.toggle('access')
    await api.toggle('sharing')
    expect(api.isOpen('access')).toBe(true)
    expect(api.isOpen('sharing')).toBe(true)

    await api.toggle('access')
    expect(api.isOpen('access')).toBe(false)
    expect(api.isOpen('sharing')).toBe(true)
    // Persisted in canonical order.
    expect(accountApi.updateAdminNavOpenCategories).toHaveBeenLastCalledWith(['sharing'])
  })

  it('reverts + toasts on a persist failure', async () => {
    const { api, pinia } = await setup({ admin_nav_collapse_mode: 'manual' }, 'root')
    setActivePinia(pinia)
    const ui = useUiStore()
    const toast = vi.spyOn(ui, 'pushToast')
    vi.mocked(accountApi.updateAdminNavOpenCategories).mockRejectedValueOnce(
      new Error('boom'),
    )

    await api.toggle('access')
    expect(api.isOpen('access')).toBe(false) // reverted
    expect(toast).toHaveBeenCalledWith(expect.any(String), 'error')
  })
})
