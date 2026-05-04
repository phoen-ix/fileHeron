/* Minimal EventSource wrapper with auto-reconnect, Last-Event-Id, and
 * a per-connect URL factory.
 *
 * Why hand-rolled (not @vueuse/integrations): the deps surface stays
 * small and the reconnect-with-Last-Event-Id behavior is specific
 * enough that wrapping a generic helper would obscure rather than
 * clarify the lifecycle.
 *
 * The browser's EventSource attaches the Last-Event-Id header on
 * reconnect automatically when an `id:` field has been observed in
 * the stream — we cooperate with that by emitting `id:` in the
 * server's frames (see services/sse.py). On a manual reconnect we
 * pass the last seen id explicitly via a query param so the server
 * can catch up.
 *
 * URL factory: callers pass a function `(lastEventId) => Promise<string>`
 * resolved on every connect. Used by NotificationBell to mint a fresh
 * signed SSE token (EventSource can't send Authorization headers, so
 * auth rides in the URL). On error the timer backs off exponentially
 * with a cap so a token-mint failure can't loop the browser.
 */
import { onBeforeUnmount, ref } from 'vue'

export type SSEUrlFactory = (lastEventId: string | null) => string | Promise<string>

export interface UseSSEOptions {
  url: string | SSEUrlFactory
  onMessage: (event: MessageEvent) => void
  onOpen?: () => void
  onError?: (e: Event) => void
}

const RECONNECT_BASE_MS = 1500
const RECONNECT_CAP_MS = 30_000

export function useSSE(opts: UseSSEOptions) {
  const connected = ref(false)
  const lastEventId = ref<string | null>(null)
  let es: EventSource | null = null
  let reconnectTimer: number | null = null
  let reconnectAttempt = 0
  let stopped = false

  function _appendLastId(url: string): string {
    if (!lastEventId.value) return url
    const sep = url.includes('?') ? '&' : '?'
    return `${url}${sep}last_event_id=${encodeURIComponent(lastEventId.value)}`
  }

  async function _resolveUrl(): Promise<string> {
    if (typeof opts.url === 'function') {
      // Factory is responsible for whatever auth params it wants;
      // we still tack on last_event_id so server-side catch-up works.
      const base = await opts.url(lastEventId.value)
      return _appendLastId(base)
    }
    return _appendLastId(opts.url)
  }

  async function _connect() {
    if (stopped) return
    let u: string
    try {
      u = await _resolveUrl()
    } catch (err) {
      // URL factory rejected (e.g. token mint failed because the user
      // logged out). Treat as a soft error and back off.
      _scheduleReconnect()
      return
    }
    es = new EventSource(u, { withCredentials: true })
    es.onopen = () => {
      connected.value = true
      reconnectAttempt = 0  // reset backoff on successful open
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
      // (we send `: close` after 60s) it gives up. Force a reconnect
      // with exponential backoff so a misconfigured server can't be
      // hammered into the ground.
      es?.close()
      es = null
      _scheduleReconnect()
    }
  }

  function _scheduleReconnect() {
    if (stopped || reconnectTimer !== null) return
    const delay = Math.min(
      RECONNECT_CAP_MS,
      RECONNECT_BASE_MS * 2 ** Math.min(reconnectAttempt, 5),
    )
    reconnectAttempt += 1
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      void _connect()
    }, delay)
  }

  function start() {
    stopped = false
    reconnectAttempt = 0
    void _connect()
  }

  function stop() {
    stopped = true
    if (reconnectTimer !== null) {
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
