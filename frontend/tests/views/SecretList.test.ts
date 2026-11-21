/* The secrets list opens on "Active". Someone whose one secret had been read
 * and destroyed was told "You haven't sent a secret yet." - the same empty
 * state as SecretRequestList (2026-11-21). */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

type Params = { box: string; state?: string[]; q?: string; page?: number; page_size?: number }
const listSecrets = vi.hoisted(() => vi.fn())
vi.mock('@/api/secrets', () => ({ listSecrets: (p: Params) => listSecrets(p) }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: { box: 'sent' } }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

import SecretList from '@/views/SecretList.vue'
import { useAuthStore } from '@/stores/auth'
import type { MeResponse } from '@/types/api'

const burned = {
  id: 's1',
  state: 'burned',
  label: 'Door code',
  sender: { id: 1, display_name: 'Ann' },
  created_at: '2030-01-01T09:00:00',
  expires_at: '2030-01-08T09:00:00',
  ended_at: '2030-01-02T09:00:00',
  views_used: 1,
  max_views: 1,
  has_passphrase: false,
  has_link: false,
  viewer_role: 'sender',
  recipients: { users: [], groups: [], emails: [], total: 0 },
}

function boxHolding(all: Array<typeof burned>) {
  listSecrets.mockImplementation(async (p: Params) => {
    const rows = p.state?.length ? all.filter((r) => p.state!.includes(r.state)) : all
    return { data: { items: rows, total: rows.length, page: 1, page_size: p.page_size ?? 20 } }
  })
}

async function mountList() {
  setActivePinia(createPinia())
  useAuthStore().user = { id: 1, role: 'employee', can_send_secrets: true } as MeResponse
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(SecretList, {
    global: { plugins: [i18n], stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  })
  await flushPromises()
  return w
}

// Braces: a function RETURNED from beforeEach is run as its teardown.
beforeEach(() => {
  listSecrets.mockReset()
})

describe('SecretList empty state', () => {
  it('says the filter hides a secret that has ended, and shows it on request', async () => {
    boxHolding([burned])
    const w = await mountList()

    expect(w.text()).not.toContain("You haven't sent a secret yet.")
    expect(w.text()).toContain('No secret matches this filter.')

    await w.get('[data-testid="secrets-show-all"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Door code')
  })

  it('says nothing was sent yet only when the box is empty', async () => {
    boxHolding([])
    const w = await mountList()

    expect(w.text()).toContain("You haven't sent a secret yet.")
    expect(w.find('[data-testid="secrets-show-all"]').exists()).toBe(false)
  })
})
