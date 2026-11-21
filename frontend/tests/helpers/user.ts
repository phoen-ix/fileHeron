/* What a user's mouse and keyboard do to the DOM, for component tests.
 *
 * `trigger('focus')` dispatches a focus event whatever the state; a browser
 * fires none on a field that already has the focus. The recipient picker's
 * tests fired one before every typing step, which reopened its list each time
 * and hid that a second recipient could not be added (2026-11-20).
 *
 * These move the REAL focus - happy-dom's `.focus()` is a no-op on the active
 * element - so a test cannot produce an event the browser would not. The
 * component must be mounted with `attachTo: document.body`: an element outside
 * the document cannot take the focus.
 * `backend/tests/test_frontend_test_hygiene.py` keeps synthetic focus and blur
 * events out of every other test file. */
import type { DOMWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'

type Target = DOMWrapper<Element> | Element

const CONTROLS = 'input, textarea, select, button, a[href]'

function node(target: Target): HTMLElement {
  return ('element' in target ? target.element : target) as HTMLElement
}

/** A mouse click, in the order a browser runs it: mousedown; unless a handler
 *  prevented that, the focus moves to what was pressed (or leaves the active
 *  element when nothing focusable was); mouseup; click; and a click on a
 *  label's own text or box then focuses its control, unless prevented. */
export async function click(target: Target): Promise<void> {
  const el = node(target)
  const down = new MouseEvent('mousedown', { bubbles: true, cancelable: true })
  el.dispatchEvent(down)
  if (!down.defaultPrevented) {
    const to = el.closest<HTMLElement>(`${CONTROLS}, [tabindex], [contenteditable="true"]`)
    if (to) to.focus()
    else (document.activeElement as HTMLElement | null)?.blur()
  }
  el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
  const ev = new MouseEvent('click', { bubbles: true, cancelable: true })
  el.dispatchEvent(ev)
  const control = el.closest('label')?.control
  if (control && !ev.defaultPrevented && !el.closest(CONTROLS)) control.focus()
  await nextTick()
}

/** Click away: the focus leaves whatever had it. */
export function clickAway(): Promise<void> {
  return click(document.body)
}

/** Type `text` into a field, replacing what is in it. The field is clicked
 *  first only when it does not already have the focus. */
export async function type(target: Target, text: string): Promise<void> {
  const el = node(target) as HTMLInputElement
  if (document.activeElement !== el) await click(el)
  el.value = text
  el.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
}

/** Press a key on whatever has the focus. */
export async function press(key: string): Promise<void> {
  const el = (document.activeElement ?? document.body) as HTMLElement
  el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  el.dispatchEvent(new KeyboardEvent('keyup', { key, bubbles: true }))
  await nextTick()
}
