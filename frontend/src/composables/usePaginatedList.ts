/* Shared state + load skeleton for the admin list views.
 *
 * Every paginated admin table repeated the same six refs
 * (`items/total/page/pageSize/loading/errorMsg`) plus a `load()` that toggles
 * `loading`, calls one endpoint, assigns `items`/`total`, and funnels failures
 * through `useApiError().describe`. This owns that boilerplate; the caller
 * supplies a `fetcher` closure that reads its own filter/sort refs and returns
 * `{ items, total }`. View-specific filters, watchers, and row actions stay in
 * the view.
 */
import { ref, type Ref } from 'vue'

import { useApiError } from '@/composables/useApiError'

export interface PaginatedResult<T> {
  items: T[]
  total: number
}

export function usePaginatedList<T>(
  fetcher: (params: { page: number; pageSize: number }) => Promise<PaginatedResult<T>>,
  opts: { pageSize?: number } = {},
) {
  const { describe } = useApiError()
  const items = ref<T[]>([]) as Ref<T[]>
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(opts.pageSize ?? 50)
  const loading = ref(true)
  const errorMsg = ref<string | null>(null)

  async function load() {
    loading.value = true
    errorMsg.value = null
    try {
      const data = await fetcher({ page: page.value, pageSize: pageSize.value })
      items.value = data.items
      total.value = data.total
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      loading.value = false
    }
  }

  return { items, total, page, pageSize, loading, errorMsg, load }
}
