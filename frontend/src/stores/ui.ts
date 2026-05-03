import { defineStore } from 'pinia'
import { ref } from 'vue'

interface ToastMsg {
  id: number
  tone: 'info' | 'success' | 'error' | 'warn'
  text: string
}

export const useUiStore = defineStore('ui', () => {
  const toasts = ref<ToastMsg[]>([])
  let nextId = 1

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

  return { toasts, pushToast, dismiss }
})
