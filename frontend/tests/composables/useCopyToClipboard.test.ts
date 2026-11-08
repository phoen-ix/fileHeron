/* One copy helper for twelve call sites; two of them had no `try`, so a
 * blocked clipboard was an unhandled rejection (audit 2026-11-08). */
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { COPIED_FEEDBACK_MS, useCopyToClipboard } from '@/composables/useCopyToClipboard'

function host() {
  let api!: ReturnType<typeof useCopyToClipboard>
  const w = mount(
    defineComponent({
      setup() {
        api = useCopyToClipboard()
        return () => h('div')
      },
    }),
  )
  return { api, w }
}

function stubClipboard(impl: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn(impl) },
    configurable: true,
  })
}

describe('useCopyToClipboard', () => {
  afterEach(() => vi.useRealTimers())

  it('marks what was copied, then forgets it after the feedback window', async () => {
    vi.useFakeTimers()
    stubClipboard(async () => {})
    const { api } = host()

    expect(await api.copy('abc', 7)).toBe(true)
    expect(api.copiedKey.value).toBe(7)
    expect(api.copied.value).toBe(true)

    vi.advanceTimersByTime(COPIED_FEEDBACK_MS)
    expect(api.copied.value).toBe(false)
  })

  it('a blocked clipboard answers false instead of throwing', async () => {
    stubClipboard(async () => {
      throw new DOMException('denied', 'NotAllowedError')
    })
    const { api } = host()

    await expect(api.copy('abc')).resolves.toBe(false)
    expect(api.copied.value).toBe(false)
  })

  it('a second copy restarts the window rather than racing the first timer', async () => {
    vi.useFakeTimers()
    stubClipboard(async () => {})
    const { api } = host()

    await api.copy('a', 1)
    vi.advanceTimersByTime(COPIED_FEEDBACK_MS - 100)
    await api.copy('b', 2)
    vi.advanceTimersByTime(200)
    await flushPromises()
    expect(api.copiedKey.value).toBe(2)
  })
})
