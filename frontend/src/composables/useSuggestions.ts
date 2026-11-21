/* A search-as-you-type request: debounced, and only the answer to the LATEST
 * query lands.
 *
 * Five places each wrote their own timer: the recipient picker, the policy
 * allowlist, the API-token owner picker, the share-list user filter and the
 * group member search. Three had nothing to stop a slower answer for "ann"
 * overwriting the one for "annabelle", and in four an answer still in flight
 * when the user PICKED refilled the list the pick had just emptied.
 *
 * Not `useDebouncedSearch`, which debounces a list page's RELOAD and holds no
 * results of its own. */
import { onScopeDispose, ref, shallowRef } from 'vue'

export function useSuggestions<T>(
  fetch: (query: string) => Promise<T[]>,
  { delay = 200, minLength = 0 }: { delay?: number; minLength?: number } = {},
) {
  const results = shallowRef<T[]>([])
  const loading = ref(false)
  /** The query `results` answers; null until the first answer. */
  const answered = ref<string | null>(null)
  /** What the last search failed with, null once another one starts. */
  const error = shallowRef<unknown>(null)

  let timer: ReturnType<typeof setTimeout> | null = null
  let seq = 0

  /** Forget the pending search and any answer still in flight. */
  function cancel() {
    if (timer) clearTimeout(timer)
    timer = null
    seq++
    loading.value = false
  }

  /** Empty the list, and keep an answer in flight from refilling it. */
  function clear() {
    cancel()
    results.value = []
  }

  async function run(query: string) {
    const mine = ++seq
    loading.value = true
    error.value = null
    try {
      const items = await fetch(query)
      if (mine !== seq) return
      results.value = items
      answered.value = query
    } catch (err) {
      if (mine !== seq) return
      results.value = []
      error.value = err
    } finally {
      if (mine === seq) loading.value = false
    }
  }

  /** Search for `query` once the user pauses; `now` skips the pause. A query
   *  shorter than `minLength` empties the list instead. */
  function search(query: string, { now = false } = {}) {
    cancel()
    if (query.length < minLength) {
      results.value = []
      answered.value = query
      return
    }
    if (now) {
      void run(query)
      return
    }
    timer = setTimeout(() => {
      timer = null
      void run(query)
    }, delay)
  }

  onScopeDispose(cancel)

  return { results, loading, answered, error, search, clear, cancel }
}
