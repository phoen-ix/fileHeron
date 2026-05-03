import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useScrollSpy } from '@/composables/useScrollSpy'

// Stub the global IntersectionObserver so the composable runs in happy-dom
// without throwing. We capture the callback + observed targets so each test
// can drive intersection events deterministically.
type ObsCallback = (entries: IntersectionObserverEntry[]) => void

let lastCallback: ObsCallback | null = null
let lastObserver: FakeObserver | null = null

class FakeObserver {
  callback: ObsCallback
  targets = new Set<Element>()
  disconnected = false
  constructor(cb: ObsCallback) {
    this.callback = cb
    lastCallback = cb
    lastObserver = this
  }
  observe(el: Element) {
    this.targets.add(el)
  }
  unobserve(el: Element) {
    this.targets.delete(el)
  }
  disconnect() {
    this.disconnected = true
    this.targets.clear()
  }
}

function fireIntersection(id: string, isIntersecting: boolean) {
  const target = document.getElementById(id)
  if (!target) throw new Error(`element #${id} not found`)
  const entry = {
    target,
    isIntersecting,
    intersectionRatio: isIntersecting ? 1 : 0,
  } as unknown as IntersectionObserverEntry
  lastCallback?.([entry])
}

function makeHost(sectionIds: () => string[]) {
  return defineComponent({
    setup() {
      const handle = useScrollSpy(sectionIds, {
        topOffsetPx: 0,
        bottomOffsetVh: 0,
      })
      return { handle }
    },
    render() {
      return h('div', this.handle.active.value)
    },
  })
}

describe('useScrollSpy', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('IntersectionObserver', FakeObserver)
    document.body.innerHTML = `
      <section id="profile">profile</section>
      <section id="password">password</section>
      <section id="sessions">sessions</section>
    `
    lastCallback = null
    lastObserver = null
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('seeds active to the first id when nothing is intersecting yet', async () => {
    const ids = ref(['profile', 'password', 'sessions'])
    const wrapper = mount(makeHost(() => ids.value))
    await nextTick()
    expect(wrapper.vm.handle.active.value).toBe('profile')
  })

  it('updates active to the topmost intersecting section', async () => {
    const ids = ref(['profile', 'password', 'sessions'])
    const wrapper = mount(makeHost(() => ids.value))
    await nextTick()

    fireIntersection('password', true)
    expect(wrapper.vm.handle.active.value).toBe('password')

    // Both visible — declared order wins, so 'password' beats 'sessions'.
    fireIntersection('sessions', true)
    expect(wrapper.vm.handle.active.value).toBe('password')

    // Password leaves the viewport — sessions takes over.
    fireIntersection('password', false)
    expect(wrapper.vm.handle.active.value).toBe('sessions')
  })

  it('lockTo holds the active id past observer events for the configured ms', async () => {
    const ids = ref(['profile', 'password', 'sessions'])
    const wrapper = mount(makeHost(() => ids.value))
    await nextTick()

    wrapper.vm.handle.lockTo('sessions', 500)
    expect(wrapper.vm.handle.active.value).toBe('sessions')

    // While locked, intersection events must not steal the active id.
    fireIntersection('password', true)
    expect(wrapper.vm.handle.active.value).toBe('sessions')

    vi.advanceTimersByTime(500)
    // After the lock expires, the next intersection event takes effect.
    fireIntersection('profile', true)
    expect(wrapper.vm.handle.active.value).toBe('profile')
  })

  it('rebuilds the observer when the section list changes', async () => {
    const ids = ref(['profile', 'password'])
    mount(makeHost(() => ids.value))
    await nextTick()
    const first = lastObserver
    expect(first).not.toBeNull()
    expect(first!.disconnected).toBe(false)

    ids.value = ['profile', 'password', 'sessions']
    await nextTick()
    // First observer torn down, a fresh one created.
    expect(first!.disconnected).toBe(true)
    expect(lastObserver).not.toBe(first)
    expect(lastObserver!.targets.size).toBe(3)
  })

  it('disconnects the observer on unmount', async () => {
    const wrapper = mount(makeHost(() => ['profile']))
    await nextTick()
    const obs = lastObserver
    wrapper.unmount()
    expect(obs!.disconnected).toBe(true)
  })

  it('forces the last id active when scrolled to document bottom', async () => {
    const ids = ref(['profile', 'password', 'sessions'])
    const wrapper = mount(makeHost(() => ids.value))
    await nextTick()

    // Pretend the observer most recently flagged 'password' as active.
    fireIntersection('password', true)
    expect(wrapper.vm.handle.active.value).toBe('password')

    // Simulate scroll-to-bottom: window.scrollY + innerHeight >= scrollHeight.
    Object.defineProperty(window, 'scrollY', { value: 800, configurable: true })
    Object.defineProperty(window, 'innerHeight', { value: 600, configurable: true })
    Object.defineProperty(document.documentElement, 'scrollHeight', {
      value: 1400,
      configurable: true,
    })
    window.dispatchEvent(new Event('scroll'))
    expect(wrapper.vm.handle.active.value).toBe('sessions')
  })

  it('does not override active during a click-scroll lock even at bottom', async () => {
    const ids = ref(['profile', 'password', 'sessions'])
    const wrapper = mount(makeHost(() => ids.value))
    await nextTick()

    // Click locked us to 'profile' (e.g. user clicked nav while at bottom).
    wrapper.vm.handle.lockTo('profile', 500)

    Object.defineProperty(window, 'scrollY', { value: 800, configurable: true })
    Object.defineProperty(window, 'innerHeight', { value: 600, configurable: true })
    Object.defineProperty(document.documentElement, 'scrollHeight', {
      value: 1400,
      configurable: true,
    })
    window.dispatchEvent(new Event('scroll'))
    expect(wrapper.vm.handle.active.value).toBe('profile')
  })
})
