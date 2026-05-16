import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const SEQUENCE_TIMEOUT_MS = 800

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (el.isContentEditable) return true
  return false
}

function focusFirstSearch(): boolean {
  const candidates = [
    'input[type="search"]',
    'input[role="searchbox"]',
    'input[name="search"]',
    'input[name="q"]',
    'input[placeholder*="search" i]',
    'input[placeholder*="suchen" i]',
    'input[placeholder*="search by" i]',
  ]
  for (const sel of candidates) {
    const el = document.querySelector<HTMLInputElement>(sel)
    if (el && !el.disabled && el.offsetParent !== null) {
      el.focus()
      el.select()
      return true
    }
  }
  return false
}

export function useKeyboardShortcuts() {
  const router = useRouter()
  const auth = useAuthStore()
  const cheatSheetOpen = ref(false)
  let pendingPrefix: 'g' | null = null
  let pendingTimer: ReturnType<typeof setTimeout> | null = null

  function clearPending() {
    pendingPrefix = null
    if (pendingTimer) {
      clearTimeout(pendingTimer)
      pendingTimer = null
    }
  }

  function armPending(prefix: 'g') {
    pendingPrefix = prefix
    if (pendingTimer) clearTimeout(pendingTimer)
    pendingTimer = setTimeout(() => clearPending(), SEQUENCE_TIMEOUT_MS)
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.ctrlKey || e.metaKey || e.altKey) return
    if (isTypingTarget(e.target)) {
      if (e.key === 'Escape' && cheatSheetOpen.value) {
        cheatSheetOpen.value = false
        e.preventDefault()
      }
      return
    }

    if (cheatSheetOpen.value && e.key === 'Escape') {
      cheatSheetOpen.value = false
      e.preventDefault()
      return
    }

    if (pendingPrefix === 'g') {
      let dest: string | null = null
      if (e.key === 'i') dest = '/inbox'
      else if (e.key === 'o') dest = '/outbox'
      else if (e.key === 'a') dest = '/account'
      else if (e.key === 'n') dest = '/share/new'
      clearPending()
      if (dest && auth.isAuthenticated) {
        e.preventDefault()
        void router.push(dest)
      }
      return
    }

    if (e.key === '?' || (e.key === '/' && e.shiftKey)) {
      cheatSheetOpen.value = !cheatSheetOpen.value
      e.preventDefault()
      return
    }

    if (e.key === '/') {
      if (focusFirstSearch()) e.preventDefault()
      return
    }

    if (!auth.isAuthenticated) return

    if (e.key === 'n') {
      e.preventDefault()
      void router.push('/share/new')
      return
    }

    if (e.key === 'g') {
      armPending('g')
      e.preventDefault()
      return
    }
  }

  onMounted(() => {
    window.addEventListener('keydown', onKeydown)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('keydown', onKeydown)
    if (pendingTimer) clearTimeout(pendingTimer)
  })

  return { cheatSheetOpen }
}
