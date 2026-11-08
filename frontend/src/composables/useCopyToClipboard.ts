/* Copy text, and remember for a moment what was copied so the button can say
 * so.
 *
 * Twelve components each wrote their own: `navigator.clipboard.writeText` in a
 * try, a `copied` flag or a per-row id, a 1600 ms timer (cleared on unmount in
 * one of them), and two with no `try` at all, so a blocked clipboard was an
 * unhandled rejection (audit 2026-11-08). `copy` never throws: it answers
 * whether the text reached the clipboard, and a blocked clipboard leaves the
 * text on screen to select by hand, which every caller already shows. */
import { computed, onBeforeUnmount, ref } from 'vue'

export const COPIED_FEEDBACK_MS = 1600

export function useCopyToClipboard() {
  /** What was copied last (a row id, or `true` for a single button), until
   *  the feedback window passes. */
  const copiedKey = ref<string | number | true | null>(null)
  let timer: number | null = null

  async function copy(text: string, key: string | number | true = true): Promise<boolean> {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      return false
    }
    copiedKey.value = key
    if (timer !== null) window.clearTimeout(timer)
    timer = window.setTimeout(() => {
      copiedKey.value = null
      timer = null
    }, COPIED_FEEDBACK_MS)
    return true
  }

  onBeforeUnmount(() => {
    if (timer !== null) window.clearTimeout(timer)
  })

  return { copy, copied: computed(() => copiedKey.value !== null), copiedKey }
}
