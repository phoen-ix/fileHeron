/* When the list under a search field is open.
 *
 * The recipient picker and the admin search each kept their own flag and
 * timer, and both opened the list only when the field GAINED the focus. After
 * a pick or Escape the field still has it, so typing found no list: a second
 * recipient could not be added (2026-11-20), and the admin search stayed shut.
 * Both also let the delayed close after a blur run when the focus had already
 * come back - pressing a field's own label does exactly that.
 *
 * One rule: `show` on focus and on typing, `close` on Escape and on a pick,
 * `closeSoon` on blur - late enough for a mousedown on an option to run first,
 * and dropped if the list is shown again meanwhile. */
import { onScopeDispose, ref } from 'vue'

export const BLUR_CLOSE_MS = 120

export function usePopupList() {
  const open = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null

  function cancelClose() {
    if (timer) clearTimeout(timer)
    timer = null
  }

  function show() {
    cancelClose()
    open.value = true
  }

  function close() {
    cancelClose()
    open.value = false
  }

  function closeSoon() {
    cancelClose()
    timer = setTimeout(close, BLUR_CLOSE_MS)
  }

  onScopeDispose(cancelClose)

  return { open, show, close, closeSoon }
}
