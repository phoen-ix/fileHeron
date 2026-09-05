import { defineStore } from 'pinia'
import { ref } from 'vue'

interface ToastMsg {
  id: number
  tone: 'info' | 'success' | 'error' | 'warn'
  text: string
}

interface ConfirmOptions {
  message: string
  title?: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}

interface ConfirmState extends ConfirmOptions {
  open: boolean
}

export const useUiStore = defineStore('ui', () => {
  const toasts = ref<ToastMsg[]>([])
  let nextId = 1

  // Promise-based confirm dialog. `confirm()` resolves true/false when the
  // user acts; a single <ConfirmDialog> host (mounted in App.vue) renders it.
  const confirmState = ref<ConfirmState | null>(null)
  let confirmResolver: ((ok: boolean) => void) | null = null

  function confirm(opts: ConfirmOptions): Promise<boolean> {
    // Resolve any in-flight prompt as cancelled before opening a new one.
    if (confirmResolver) confirmResolver(false)
    confirmState.value = { ...opts, open: true }
    return new Promise<boolean>((resolve) => {
      confirmResolver = resolve
    })
  }

  function resolveConfirm(ok: boolean) {
    confirmState.value = null
    if (confirmResolver) {
      confirmResolver(ok)
      confirmResolver = null
    }
  }

  function pushToast(text: string, tone: ToastMsg['tone'] = 'info', timeout = 4000) {
    const id = nextId++
    toasts.value.push({ id, tone, text })
    if (timeout > 0) {
      setTimeout(() => {
        toasts.value = toasts.value.filter((t) => t.id !== id)
      }, timeout)
    }
  }

  function dismiss(id: number) {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  return { toasts, pushToast, dismiss, confirmState, confirm, resolveConfirm }
})
