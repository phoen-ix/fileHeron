/* The shared list skeleton owns the two things every hand-rolled copy got
 * subtly wrong: the out-of-order guard (a slow early response must not
 * overwrite a newer one) and the page watcher (a filter change goes back to
 * page 1 and reloads exactly ONCE, not twice). Ten views rolled their own
 * loader until 2026-11-08, five of them with no guard at all. */
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'
import { usePaginatedList } from '@/composables/usePaginatedList'

type Row = { id: number }
type Fetcher = Parameters<typeof usePaginatedList<Row>>[0]
type Opts = Parameters<typeof usePaginatedList<Row>>[1]

/* `useApiError` (the composable's error mapper) needs an i18n setup context, so
 * the composable is hosted in a throwaway component. */
function host(fetcher: Fetcher, opts?: Opts) {
  let list!: ReturnType<typeof usePaginatedList<Row>>
  const Host = defineComponent({
    setup() {
      list = usePaginatedList<Row>(fetcher, opts)
      return () => h('div')
    },
  })
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  mount(Host, { global: { plugins: [i18n] } })
  return list
}

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

describe('usePaginatedList', () => {
  it('discards a response that arrives after a newer request was started', async () => {
    const first = deferred<{ items: Row[]; total: number }>()
    const second = deferred<{ items: Row[]; total: number }>()
    const fetcher = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const list = host(fetcher)

    void list.load()
    void list.load()
    second.resolve({ items: [{ id: 2 }], total: 1 })
    await flushPromises()
    first.resolve({ items: [{ id: 1 }], total: 1 }) // the slow early one lands last
    await flushPromises()

    expect(list.items.value).toEqual([{ id: 2 }])
    expect(list.loading.value).toBe(false)
  })

  it('resetAndLoad from a later page fetches page 1 exactly once', async () => {
    const fetcher = vi.fn(async ({ page }: { page: number }) => ({
      items: [{ id: page }],
      total: 100,
    }))
    const list = host(fetcher)
    list.page.value = 3
    await flushPromises()
    expect(fetcher).toHaveBeenLastCalledWith({ page: 3, pageSize: 50 })
    fetcher.mockClear()

    list.resetAndLoad()
    await flushPromises()

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher).toHaveBeenCalledWith({ page: 1, pageSize: 50 })
    expect(list.page.value).toBe(1)
  })

  it('resetAndLoad on page 1 still reloads, exactly once', async () => {
    const fetcher = vi.fn(async () => ({ items: [], total: 0 }))
    const list = host(fetcher)

    list.resetAndLoad()
    await flushPromises()

    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('a page change loads through the owned watcher, and total falls back to the row count', async () => {
    const fetcher = vi.fn(async ({ page }: { page: number }) => ({
      items: [{ id: page }, { id: page + 10 }],
    }))
    const onLoaded = vi.fn()
    const list = host(fetcher, { pageSize: 25, onLoaded })

    list.page.value = 2
    await flushPromises()

    expect(fetcher).toHaveBeenCalledWith({ page: 2, pageSize: 25 })
    expect(list.total.value).toBe(2)
    expect(onLoaded).toHaveBeenCalledTimes(1)
  })

  it('a failed load shows the error and clears loading', async () => {
    const fetcher = vi.fn(async () => {
      throw {
        isAxiosError: true,
        response: { status: 500, data: { code: 'INTERNAL_ERROR', error: 'boom' } },
      }
    })
    const list = host(fetcher)

    await list.load()

    expect(list.errorMsg.value).toBeTruthy()
    expect(list.loading.value).toBe(false)
  })
})
