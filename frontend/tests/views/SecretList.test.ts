/* Each box opens on what it is for. Sent is where you follow a secret, so it
 * shows them all: it opened on "Active", and someone whose one secret had been
 * read and destroyed was told "You haven't sent a secret yet." Received is
 * what can still be opened, so it keeps "Active" - and says when that hides
 * what is there (2026-11-21, 2026-11-22). */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

type Params = { box: string; state?: string[]; q?: string; page?: number; page_size?: number }
const listSecrets = vi.hoisted(() => vi.fn())
vi.mock('@/api/secrets', () => ({ listSecrets: (p: Params) => listSecrets(p) }))
const route = vi.hoisted(() => ({ query: { box: 'sent' } }))
vi.mock('vue-router', () => ({
  useRoute: () => route,
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

async function mountList(box: 'sent' | 'received') {
  route.query = { box }
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

describe('SecretList', () => {
  it('shows every secret I sent, an ended one included', async () => {
    boxHolding([burned])
    const w = await mountList('sent')

    expect((w.get('select').element as HTMLSelectElement).value).toBe('all')
    expect(w.text()).toContain('Door code')
    expect(w.text()).not.toContain("You haven't sent a secret yet.")
  })

  it('opens Received on what can still be opened, and says when that hides what is there', async () => {
    boxHolding([burned])
    const w = await mountList('received')

    expect((w.get('select').element as HTMLSelectElement).value).toBe('active')
    expect(w.text()).not.toContain('No secrets for you.')
    expect(w.text()).toContain('No secret matches this filter.')

    await w.get('[data-testid="secrets-show-all"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Door code')
  })

  it('says there is nothing only when the box is empty', async () => {
    boxHolding([])
    expect((await mountList('sent')).text()).toContain("You haven't sent a secret yet.")
    const received = await mountList('received')
    expect(received.text()).toContain('No secrets for you.')
    expect(received.find('[data-testid="secrets-show-all"]').exists()).toBe(false)
  })
})
