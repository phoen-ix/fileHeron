/* The request list opens on "Open". Someone whose one request had just been
 * answered - it is "Closed" now - was told "You haven't asked for a secret
 * yet." (found by the secret-request use case, 2026-11-21). An empty list says
 * that only when the box really is empty; otherwise it says the filter hides
 * what is there, and offers to show it. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

type Params = { box: string; state?: string[]; q?: string; page?: number; page_size?: number }
const listSecretRequests = vi.hoisted(() => vi.fn())
vi.mock('@/api/secretRequests', () => ({
  listSecretRequests: (p: Params) => listSecretRequests(p),
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  RouterLink: { template: '<a><slot /></a>' },
}))

import SecretRequestList from '@/components/SecretRequestList.vue'

const answered = {
  id: 'r1',
  state: 'fulfilled',
  closed_reason: null,
  label: 'Router password',
  requester: { id: 1, display_name: 'Ann' },
  created_at: '2030-01-01T09:00:00',
  expires_at: '2030-01-08T09:00:00',
  viewer_role: 'requester',
  targets: [],
}

/** A box holding `all`; a state filter returns the rows in those states. */
function boxHolding(all: Array<typeof answered>) {
  listSecretRequests.mockImplementation(async (p: Params) => {
    const rows = p.state?.length ? all.filter((r) => p.state!.includes(r.state)) : all
    return { data: { items: rows, total: rows.length, page: 1, page_size: p.page_size ?? 20 } }
  })
}

async function mountList() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(SecretRequestList, {
    props: { canRequest: true },
    global: { plugins: [i18n], stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  })
  await flushPromises()
  return w
}

// Braces: a function RETURNED from beforeEach is run as its teardown.
beforeEach(() => {
  listSecretRequests.mockReset()
})

describe('SecretRequestList empty state', () => {
  it('says the filter hides a request that was answered, and shows it on request', async () => {
    boxHolding([answered])
    const w = await mountList()

    expect(w.text()).not.toContain("You haven't asked for a secret yet.")
    expect(w.text()).toContain('No request matches this filter.')

    await w.get('[data-testid="requests-show-all"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Router password')
    expect((w.get('select').element as HTMLSelectElement).value).toBe('all')
  })

  it('says nothing was asked yet only when the box is empty', async () => {
    boxHolding([])
    const w = await mountList()

    expect(w.text()).toContain("You haven't asked for a secret yet.")
    expect(w.find('[data-testid="requests-show-all"]').exists()).toBe(false)
  })
})
