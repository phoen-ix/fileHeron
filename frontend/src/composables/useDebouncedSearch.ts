import { onScopeDispose, watch, type Ref } from 'vue'

/**
 * Debounce a reactive search source: when `source` changes, run `cb` after
 * `delay` ms of quiet. Replaces the hand-rolled timer+watch blocks across the
 * admin list views. `cb` typically resets the page and reloads.
 *
 * The pending timer is cleared when the owning scope is torn down: a keystroke
 * followed by navigating away used to fire `cb` - a request and a state write -
 * against a view that no longer existed.
 */
export function useDebouncedSearch(
  source: Ref<unknown>,
  cb: () => void,
  delay = 220,
): void {
  let timer: ReturnType<typeof setTimeout> | null = null
  watch(source, () => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(cb, delay)
  })
  onScopeDispose(() => {
    if (timer) clearTimeout(timer)
    timer = null
  })
}
