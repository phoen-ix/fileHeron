import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'

interface ScrollSpyOptions {
  topOffsetPx?: number
  bottomOffsetVh?: number
}

interface ScrollSpyHandle {
  active: Ref<string>
  rebuild: () => void
  lockTo: (id: string, ms?: number) => void
}

// Track which sections are currently in viewport and expose the topmost one
// in declared order as `active`. Used by AccountQuickNav to highlight the
// section the user is currently reading.
//
// The `lockTo` mechanism solves the click-to-scroll race: when a nav item
// is clicked, the page smooth-scrolls past several sections in turn, each
// briefly intersecting the viewport. Without a lock, the active marker
// flickers through every passing section before settling. Holding the
// active id for ~700ms after a click keeps the highlight on the destination.
export function useScrollSpy(
  sectionIds: () => string[],
  options: ScrollSpyOptions = {},
): ScrollSpyHandle {
  const active = ref<string>('')
  const visible = new Set<string>()
  let observer: IntersectionObserver | null = null
  let lockTimeout: ReturnType<typeof setTimeout> | null = null
  const lockedTo = ref<string | null>(null)

  let cachedIds: string[] = []

  function rebuild() {
    observer?.disconnect()
    visible.clear()
    cachedIds = sectionIds()
    if (typeof window === 'undefined' || typeof IntersectionObserver === 'undefined') {
      return
    }
    if (cachedIds.length === 0) {
      observer = null
      return
    }
    observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const id = (e.target as HTMLElement).id
          if (e.isIntersecting) visible.add(id)
          else visible.delete(id)
        }
        if (lockedTo.value) return
        const next = cachedIds.find((i) => visible.has(i))
        if (next) active.value = next
      },
      {
        rootMargin: `-${options.topOffsetPx ?? 80}px 0px -${options.bottomOffsetVh ?? 60}% 0px`,
        threshold: 0,
      },
    )
    for (const id of cachedIds) {
      const el = document.getElementById(id)
      if (el) observer.observe(el)
    }
    if (!active.value && cachedIds[0]) active.value = cachedIds[0]
  }

  // Scroll-edge fallback: the bottom-most sections never reach the
  // observer's "active band" (upper 40% of viewport by default) because
  // there's no further content to push them up. When the user has
  // scrolled to the document's end, force the last id active.
  function onScroll() {
    if (lockedTo.value || cachedIds.length === 0) return
    const doc = document.documentElement
    const atBottom =
      window.scrollY + window.innerHeight >= doc.scrollHeight - 4
    if (atBottom) active.value = cachedIds[cachedIds.length - 1]
    // Scrolling up from the bottom is handled by the observer:
    // sections re-enter the band and trigger normal updates.
  }

  function lockTo(id: string, ms = 700) {
    lockedTo.value = id
    active.value = id
    if (lockTimeout) clearTimeout(lockTimeout)
    lockTimeout = setTimeout(() => {
      lockedTo.value = null
      lockTimeout = null
    }, ms)
  }

  onMounted(() => {
    rebuild()
    if (typeof window !== 'undefined') {
      window.addEventListener('scroll', onScroll, { passive: true })
    }
  })
  onBeforeUnmount(() => {
    observer?.disconnect()
    if (lockTimeout) clearTimeout(lockTimeout)
    if (typeof window !== 'undefined') {
      window.removeEventListener('scroll', onScroll)
    }
  })
  // Re-bind when the section list changes (e.g. OIDC panel mounts after
  // /api/config-public resolves).
  watch(sectionIds, rebuild, { flush: 'post' })

  return { active, rebuild, lockTo }
}
