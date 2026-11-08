/* Shared state + load skeleton for the paginated list views.
 *
 * Every paginated table repeated the same six refs
 * (`items/total/page/pageSize/loading/errorMsg`) plus a `load()` that toggles
 * `loading`, calls one endpoint, assigns `items`/`total`, and funnels failures
 * through `useApiError().describe`. This owns that boilerplate; the caller
 * supplies a `fetcher` closure that reads its own filter/sort refs and returns
 * `{ items, total }`. View-specific filters, watchers, and row actions stay in
 * the view.
 *
 * It also owns the two things every hand-rolled copy got subtly wrong:
 *
 * - the out-of-order guard. A debounced search fires a request per keystroke,
 *   not serialised, and whichever response lands LAST wins - so a slow early
 *   request could overwrite the results of a newer, narrower one and leave the
 *   table showing rows that do not match the search box (audit 2026-07-30,
 *   fe-correct-11). Five views had no guard at all and six carried a copy of
 *   this one (audit 2026-11-08).
 * - the page watcher. A filter change must go back to page 1 AND reload; the
 *   idiom `page.value = 1; void load()` fired two identical requests whenever
 *   the user was past page 1 (the watcher's and the explicit one) - harmless
 *   thanks to the guard, but two round trips per keystroke. `resetAndLoad()`
 *   does it once, so a view never writes `page.value = 1` itself.
 */
import { ref, watch, type Ref } from 'vue'

import { useApiError } from '@/composables/useApiError'

interface PaginatedResult<T> {
  items: T[]
  total?: number
}

export function usePaginatedList<T>(
  fetcher: (params: { page: number; pageSize: number }) => Promise<PaginatedResult<T>>,
  opts: {
    pageSize?: number
    /** Runs after a page landed in `items` - for page-scoped UI state (an
     *  expanded row's id means nothing on the next page). */
    onLoaded?: (data: PaginatedResult<T>) => void
  } = {},
) {
  const { describe } = useApiError()
  const items = ref<T[]>([]) as Ref<T[]>
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(opts.pageSize ?? 50)
  const loading = ref(true)
  const errorMsg = ref<string | null>(null)
  // Monotonic request token: rapid page/filter changes fire overlapping load()s;
  // discard any whose response arrives after a newer one was started, so a slow
  // earlier page can't overwrite the current results.
  let seq = 0

  async function load() {
    const mine = ++seq
    loading.value = true
    errorMsg.value = null
    try {
      const data = await fetcher({ page: page.value, pageSize: pageSize.value })
      if (mine !== seq) return
      items.value = data.items
      total.value = data.total ?? data.items.length
      opts.onLoaded?.(data)
    } catch (err) {
      if (mine !== seq) return
      errorMsg.value = describe(err)
    } finally {
      if (mine === seq) loading.value = false
    }
  }

  /** Back to page 1 and reload, exactly once - for a filter or sort change. */
  function resetAndLoad() {
    if (page.value !== 1)
      page.value = 1 // the watcher below loads
    else void load()
  }

  watch(page, () => void load())

  return { items, total, page, pageSize, loading, errorMsg, load, resetAndLoad }
}
