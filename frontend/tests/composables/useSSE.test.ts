/* stop() landing while the stream token was being minted (a logout, an
 * unmount) was ignored after the await: the EventSource opened anyway, with
 * nothing left to close it, delivering the previous user's events for ~60 s. */
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useSSE } from '@/composables/useSSE'

describe('useSSE', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('does not open a stream after stop() ran during the token mint', async () => {
    const opened = vi.fn()
    vi.stubGlobal(
      'EventSource',
      class {
        constructor(url: string) {
          opened(url)
        }
        close() {}
      },
    )
    let release!: (u: string) => void
    let sse!: ReturnType<typeof useSSE>
    mount(defineComponent({
      setup() {
        sse = useSSE({
          url: () => new Promise<string>((res) => (release = res)),
          onMessage: () => {},
        })
        return () => h('div')
      },
    }))
    sse.start() // fires the connect without awaiting it
    sse.stop()
    release('/api/notifications/stream?token=t')
    await flushPromises() // let the connect continuation run to the end
    expect(opened).not.toHaveBeenCalled()
  })
})
