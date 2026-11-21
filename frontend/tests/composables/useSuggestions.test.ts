/* The one suggestion search behind every search-as-you-type field. Each case is
 * a defect one of the five hand-written copies had. */
import { flushPromises } from '@vue/test-utils'
import { effectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSuggestions } from '@/composables/useSuggestions'

/** A fetch whose answers the test releases by hand, per query. */
function manualFetch() {
  const waiting = new Map<string, (items: string[]) => void>()
  const failing = new Map<string, (err: unknown) => void>()
  const fetch = vi.fn(
    (q: string) =>
      new Promise<string[]>((resolve, reject) => {
        waiting.set(q, resolve)
        failing.set(q, reject)
      }),
  )
  return {
    fetch,
    async answer(q: string, items: string[]) {
      waiting.get(q)!(items)
      await flushPromises()
    },
    async fail(q: string) {
      failing.get(q)!(new Error('boom'))
      await flushPromises()
    },
  }
}

function setup(opts?: { delay?: number; minLength?: number }) {
  const api = manualFetch()
  const scope = effectScope()
  const s = scope.run(() => useSuggestions(api.fetch, opts))!
  return { ...api, s, scope }
}

beforeEach(() => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }))
afterEach(() => vi.useRealTimers())

describe('useSuggestions', () => {
  it('asks once the typing pauses, not per keystroke', () => {
    const { s, fetch } = setup()
    s.search('a')
    s.search('an')
    s.search('ann')
    vi.advanceTimersByTime(199)
    expect(fetch).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(fetch.mock.calls).toEqual([['ann']])
  })

  it('never lets a slower answer for an earlier query overwrite a later one', async () => {
    const { s, answer } = setup()
    s.search('ann', { now: true })
    s.search('annabelle', { now: true })
    await answer('annabelle', ['Annabelle'])
    await answer('ann', ['Ann', 'Anna', 'Annabelle'])
    expect(s.results.value).toEqual(['Annabelle'])
    expect(s.answered.value).toBe('annabelle')
    expect(s.loading.value).toBe(false)
  })

  it('drops an answer that arrives after the list was cleared by a pick', async () => {
    const { s, answer } = setup()
    s.search('ann', { now: true })
    expect(s.loading.value).toBe(true)
    s.clear()
    expect(s.loading.value).toBe(false)
    await answer('ann', ['Ann'])
    expect(s.results.value).toEqual([])
  })

  it('empties the list for a query shorter than the minimum, without asking', async () => {
    const { s, fetch, answer } = setup({ minLength: 2 })
    s.search('an', { now: true })
    await answer('an', ['Ann'])
    s.search('a')
    vi.advanceTimersByTime(500)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(s.results.value).toEqual([])
    expect(s.answered.value).toBe('a')
  })

  it('reports a failed search and shows an empty list', async () => {
    const { s, fail, answer } = setup()
    s.search('ann', { now: true })
    await answer('ann', ['Ann'])
    s.search('anna', { now: true })
    await fail('anna')
    expect(s.results.value).toEqual([])
    expect(s.error.value).toBeInstanceOf(Error)
    expect(s.answered.value).toBe('ann')
    expect(s.loading.value).toBe(false)
  })

  it('asks nothing after its owner is gone', () => {
    const { s, fetch, scope } = setup()
    s.search('ann')
    scope.stop()
    vi.advanceTimersByTime(500)
    expect(fetch).not.toHaveBeenCalled()
  })
})
