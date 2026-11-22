/* Each box opens on what it is for. "Asked by me" is where you follow a
 * request, so it shows them all: it opened on "Open", and someone whose one
 * request had just been answered - "Closed" now - was told "You haven't asked
 * for a secret yet." (found by the secret-request use case, 2026-11-21).
 * "Asked of me" is what is left to do, so it keeps "Open"; when that hides
 * everything the list says so and offers to show it, and says "nobody has
 * asked" only when the box really is empty. */
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

const filter = (w: Awaited<ReturnType<typeof mountList>>) =>
  (w.get('select').element as HTMLSelectElement).value

describe('SecretRequestList', () => {
  it('shows every request I made, an answered one included', async () => {
    boxHolding([answered])
    const w = await mountList()

    expect(filter(w)).toBe('all')
    expect(w.text()).toContain('Router password')
    expect(w.text()).not.toContain("You haven't asked for a secret yet.")
  })

  it('opens "Asked of me" on what is still open, and says when that hides what is there', async () => {
    boxHolding([answered])
    const w = await mountList()
    await w.get('[data-testid="requests-asked"]').trigger('click')
    await flushPromises()

    expect(filter(w)).toBe('open')
    expect(w.text()).not.toContain('Nobody has asked you for a secret.')
    expect(w.text()).toContain('No request matches this filter.')

    await w.get('[data-testid="requests-show-all"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Router password')
    expect(filter(w)).toBe('all')

    // Back on "Asked by me" and over again: each box returns to its own default.
    await w.get('[data-testid="requests-mine"]').trigger('click')
    await w.get('[data-testid="requests-asked"]').trigger('click')
    await flushPromises()
    expect(filter(w)).toBe('open')
  })

  it('says nothing was asked only when the box is empty', async () => {
    boxHolding([])
    const w = await mountList()
    expect(w.text()).toContain("You haven't asked for a secret yet.")
    await w.get('[data-testid="requests-asked"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Nobody has asked you for a secret.')
    expect(w.find('[data-testid="requests-show-all"]').exists()).toBe(false)
  })

  it('loads once per box switch, with that box and its filter', async () => {
    boxHolding([answered])
    const w = await mountList()
    listSecretRequests.mockClear()
    await w.get('[data-testid="requests-asked"]').trigger('click')
    await flushPromises()

    const pages = listSecretRequests.mock.calls
      .map(([p]) => p as Params)
      .filter((p) => p.page_size !== 1)
    expect(pages.map((p) => [p.box, p.state])).toEqual([['asked', ['open']]])
  })
})
