/* Minimal EventSource wrapper with auto-reconnect and Last-Event-Id.
 *
 * Why hand-rolled (not @vueuse/integrations): the deps surface stays
 * small and the reconnect-with-Last-Event-Id behavior is specific
 * enough that wrapping a generic helper would obscure rather than
 * clarify the lifecycle. ~70 lines we can read top to bottom.
 *
 * The browser's EventSource attaches the Last-Event-Id header on
 * reconnect automatically when an `id:` field has been observed in
 * the stream — we cooperate with that by emitting `id:` in the
 * server's frames (see services/sse.py). On a manual reconnect we
 * pass the last seen id explicitly via a query param so the server
 * can catch up. */
import { onBeforeUnmount, ref } from 'vue'

export interface UseSSEOptions {
  url: string
  onMessage: (event: MessageEvent) => void
  onOpen?: () => void
  onError?: (e: Event) => void
}

export function useSSE(opts: UseSSEOptions) {
  const connected = ref(false)
  const lastEventId = ref<string | null>(null)
  let es: EventSource | null = null
  let reconnectTimer: number | null = null
  let stopped = false

  function _connect() {
    if (stopped) return
    const u = lastEventId.value
      ? `${opts.url}${opts.url.includes('?') ? '&' : '?'}last_event_id=${encodeURIComponent(lastEventId.value)}`
      : opts.url
    es = new EventSource(u, { withCredentials: true })
    es.onopen = () => {
      connected.value = true
      opts.onOpen?.()
    }
    es.onmessage = (e) => {
      if (e.lastEventId) lastEventId.value = e.lastEventId
      opts.onMessage(e)
    }
    // Custom event types: forward as MessageEvent.
    es.addEventListener('notification', (e) => {
      const me = e as MessageEvent
      if (me.lastEventId) lastEventId.value = me.lastEventId
      opts.onMessage(me)
    })
    es.onerror = (e) => {
      connected.value = false
      opts.onError?.(e)
      // The browser auto-reconnects, but on a clean server-side close
      // (we send `: close` after 60s) it gives up. Force a reconnect.
      es?.close()
      es = null
      if (!stopped) {
        reconnectTimer = window.setTimeout(_connect, 1500)
      }
    }
  }

  function start() {
    stopped = false
    _connect()
  }

  function stop() {
    stopped = true
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    es?.close()
    es = null
    connected.value = false
  }

  onBeforeUnmount(stop)

  return { connected, lastEventId, start, stop }
}
