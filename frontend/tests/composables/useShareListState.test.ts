/* Switching between outbox and inbox resets every filter at once. Each filter
 * has its own watcher that reloads the list, so one navigation used to fire
 * four to six requests, all but the last discarded by the sequence guard.
 * The `resetting` flag collapses them into exactly one. */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, defineComponent, ref } from 'vue'
import { createI18n } from 'vue-i18n'

import en from '@/i18n/locales/en.json'

vi.mock('@/api/shares', () => ({ listShares: vi.fn() }))
vi.mock('@/api/groups', () => ({ listGroups: vi.fn() }))
vi.mock('@/api/users', () => ({ searchUsers: vi.fn() }))

import { listShares } from '@/api/shares'
import { useShareListState } from '@/composables/useShareListState'

const listSharesMock = vi.mocked(listShares)

function setup() {
  const box = ref<'outbox' | 'inbox'>('outbox')
  let state!: ReturnType<typeof useShareListState>
  const Host = defineComponent({
    setup() {
      state = useShareListState(computed(() => box.value))
      return () => null
    },
  })
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  mount(Host, { global: { plugins: [i18n] } })
  return { box, state }
}

// The subject search is debounced by 250ms; wait it out with the real clock
// (fake timers would also freeze flushPromises' scheduler).
async function settle() {
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 300))
  await flushPromises()
}

beforeEach(() => {
  listSharesMock.mockReset()
  listSharesMock.mockResolvedValue({ data: { items: [], total: 0 } } as never)
})

describe('useShareListState box switch', () => {
  it('reloads exactly once and resets every filter', async () => {
    const { box, state } = setup()

    // Dirty every filter that has a reloading watcher, so each would fire on
    // the reset without the guard.
    state.stateFilter.value = 'expired'
    state.partyKind.value = 'user'
    state.sort.toggle('subject')
    state.page.value = 2
    state.subjectQuery.value = 'quarterly'
    await settle()
    expect(listSharesMock.mock.calls.length).toBeGreaterThan(0)

    listSharesMock.mockClear()
    box.value = 'inbox'
    await settle()

    expect(listSharesMock).toHaveBeenCalledTimes(1)
    expect(listSharesMock.mock.calls[0]?.[0]).toMatchObject({
      box: 'inbox',
      page: 1,
      state: ['active'],
      sort: 'created_at',
      direction: 'desc',
    })
    expect(listSharesMock.mock.calls[0]?.[0]).not.toHaveProperty('q')
    expect(state.stateFilter.value).toBe('active')
    expect(state.partyKind.value).toBe('any')
    expect(state.page.value).toBe(1)
    expect(state.subjectQuery.value).toBe('')
  })

  it('drops the guard afterwards, so ordinary filter changes still reload', async () => {
    const { box, state } = setup()
    box.value = 'inbox'
    await settle()

    listSharesMock.mockClear()
    state.stateFilter.value = 'expired'
    await settle()
    expect(listSharesMock).toHaveBeenCalledTimes(1)

    listSharesMock.mockClear()
    state.clearParty()
    await settle()
    expect(listSharesMock).toHaveBeenCalledTimes(1)
  })
})
