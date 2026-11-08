/* A "Saved" mark that shows for a moment after each save. The two
 * notification-preference screens each carried this timer, uncleared on
 * unmount (audit 2026-11-08); a second save inside the window restarts it. */
import { onBeforeUnmount, ref } from 'vue'

const SHOW_MS = 1700

export function useSavedFlash() {
  const savedAt = ref<number | null>(null)
  let timer: number | null = null

  function flashSaved() {
    savedAt.value = Date.now()
    if (timer !== null) window.clearTimeout(timer)
    timer = window.setTimeout(() => {
      savedAt.value = null
      timer = null
    }, SHOW_MS)
  }

  onBeforeUnmount(() => {
    if (timer !== null) window.clearTimeout(timer)
  })

  return { savedAt, flashSaved }
}
