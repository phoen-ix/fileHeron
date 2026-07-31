import { onBeforeUnmount, watch, type Ref } from 'vue'

/**
 * Close a modal on Escape, from anywhere.
 *
 * Every modal in the app carried `@keydown.escape` on its BACKDROP div. That
 * handler can never fire: the backdrop is not focusable, so it is never the
 * event target and never on the propagation path from whatever inside the modal
 * does have focus. Escape did nothing in any of them, in an app whose modals
 * are the only way to reach several destructive actions (audit 2026-07-30,
 * fe-i18n-a11y-7).
 *
 * A document-level listener, bound only while the modal is open, is what
 * actually works. `capture` is deliberate: it runs before a nested control that
 * stops propagation (the rich-text editor does) can swallow the key.
 *
 * @param isOpen reactive "is the modal showing"
 * @param close  what to do on Escape
 */
export function useEscapeToClose(isOpen: Ref<boolean>, close: () => void): void {
  function onKeydown(e: KeyboardEvent) {
    if (e.key !== 'Escape') return
    e.stopPropagation()
    close()
  }

  function bind() {
    document.addEventListener('keydown', onKeydown, true)
  }
  function unbind() {
    document.removeEventListener('keydown', onKeydown, true)
  }

  watch(
    isOpen,
    (open) => {
      unbind()
      if (open) bind()
    },
    { immediate: true },
  )

  onBeforeUnmount(unbind)
}
